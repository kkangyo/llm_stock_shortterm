"""뉴스 수집 -> 로컬 LLM 1차 필터(Ollama/OpenVINO) -> Claude 2차 분석 파이프라인.

주의: 이 모듈은 '호재 후보 탐지'까지만 수행한다. 실제 매수/매도 주문은
아직 구현하지 않았으며, 이후 증권사 API 연동 단계에서 별도로 추가한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.logging import RichHandler

from stock_news_bot.collectors.article_fetcher import fetch_article_text
from stock_news_bot.collectors.news_collector import NewsCollector
from stock_news_bot.config import settings
from stock_news_bot.llm.claude_client import ClaudeAnalyzer
from stock_news_bot.llm.factory import create_local_filter
from stock_news_bot.matcher.stock_matcher import StockMatcher
from stock_news_bot.models.schemas import PipelineRecord, RecommendedAction, Sentiment
from stock_news_bot.storage.db import ResultStore
from stock_news_bot.timeutil import utcnow

console = Console()
logger = logging.getLogger(__name__)


def setup_logging() -> None:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=settings.log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(console=console, rich_tracebacks=True),
            logging.FileHandler(settings.log_file, encoding="utf-8"),
        ],
    )


def is_market_open(now: datetime | None = None) -> bool:
    tz = ZoneInfo(settings.market_timezone)
    now = now or datetime.now(tz)

    if settings.weekdays_only and now.weekday() >= 5:  # 5=토, 6=일
        return False

    start_h, start_m = map(int, settings.market_start.split(":"))
    end_h, end_m = map(int, settings.market_end.split(":"))
    start, end = time(start_h, start_m), time(end_h, end_m)
    return start <= now.time() <= end


class NewsPipeline:
    def __init__(self):
        self.store = ResultStore(settings.sqlite_path)
        self.collector = NewsCollector()
        self.local_filter = create_local_filter()
        self.stock_matcher = StockMatcher()
        self.claude_analyzer: ClaudeAnalyzer | None = None

        # 프로세스 재시작 시 최근 N일치 처리 이력을 DB에서 불러와 dedup 시드로 등록한다.
        # 안 그러면 RSS가 다시 나열하는 최근 기사를 "신규"로 잘못 인식해서 Ollama/Claude를
        # 중복으로 다시 호출하게 된다.
        cutoff = utcnow() - timedelta(days=settings.dedup_lookback_days)
        recent_ids = self.store.get_recent_news_ids(cutoff)
        self.collector.preload_seen(recent_ids)
        logger.info(
            "재시작 dedup: 최근 %d일치 %d건을 DB에서 불러와 스킵 대상으로 등록",
            settings.dedup_lookback_days,
            len(recent_ids),
        )

    def _get_claude(self) -> ClaudeAnalyzer:
        # API 키가 없으면 여기서 바로 에러를 내도록 지연 초기화
        if self.claude_analyzer is None:
            self.claude_analyzer = ClaudeAnalyzer()
        return self.claude_analyzer

    def _reclassify_with_article_body(self, news):
        """1차 필터가 종목을 못 찾았을 때 기사 본문을 가져와 다시 판단해본다.

        RSS의 title/summary만으로는 정보가 부족한 경우가 많다(예: 요약이 비어있거나
        "○○○에 베팅했다" 같은 유료 기사 티저 제목). 실패한 뉴스에 한해서만 본문을
        가져오므로 폴링 주기 전체에 주는 부담은 크지 않다.
        """
        article_text = fetch_article_text(news.url)
        if not article_text:
            return None

        enriched_news = news.model_copy(update={"summary": article_text})
        enriched_result = self.local_filter.classify(enriched_news)
        logger.info(
            "[본문 보강 재판단] %s -> is_relevant=%s, candidate_tickers=%s",
            news.title,
            enriched_result.is_relevant,
            enriched_result.candidate_tickers,
        )
        return enriched_result

    def run_once(self) -> list[PipelineRecord]:
        """한 사이클 실행: 신규 뉴스 수집 -> 필터 -> 분석 -> 저장.

        반환값은 이번 사이클에서 처리한 전체 레코드 (매수 후보 아닌 것 포함).
        """
        records: list[PipelineRecord] = []
        new_items = self.collector.fetch_new()

        for news in new_items:
            filter_result = self.local_filter.classify(news)
            record = PipelineRecord(news=news, ollama_result=filter_result)

            if not filter_result.is_relevant:
                logger.debug("1차 필터 통과 못함: %s", news.title)
                self.store.save(record)
                records.append(record)
                continue

            logger.info("[1차 통과] %s | %s", filter_result.sentiment.value, news.title)

            # 1차 필터가 뽑은 종목명 후보가 실제 코스피/코스닥 상장 종목인지 대조한다.
            # 매칭에 실패하면(=실재하지 않는 종목/오인식) 이후 단계로 넘기지 않는다.
            matched_stocks = self.stock_matcher.match_many(filter_result.candidate_tickers)
            record.matched_stocks = matched_stocks

            if self.stock_matcher.available and not matched_stocks:
                enriched_result = self._reclassify_with_article_body(news)
                if enriched_result and enriched_result.is_relevant:
                    filter_result = enriched_result
                    record.ollama_result = filter_result
                    matched_stocks = self.stock_matcher.match_many(filter_result.candidate_tickers)
                    record.matched_stocks = matched_stocks

            if self.stock_matcher.available and not matched_stocks:
                logger.info(
                    "실제 상장 종목과 매칭되지 않아 후보에서 제외 (본문 보강 후에도): %s (후보명=%s)",
                    news.title,
                    filter_result.candidate_tickers,
                )
                self.store.save(record)
                records.append(record)
                continue

            if matched_stocks:
                logger.info(
                    "[종목 검증 완료] %s",
                    ", ".join(f"{m.matched_name}({m.ticker}/{m.market.value})" for m in matched_stocks),
                )
            else:
                # self.stock_matcher.available == False: KRX 목록을 못 불러온 경우.
                # 검증 자체가 불가능하므로 후보를 막지 않고 미검증 상태로 계속 진행한다.
                logger.warning("KRX 종목 목록을 불러오지 못해 검증 없이 진행합니다: %s", news.title)

            if settings.claude_enabled:
                try:
                    claude_result = self._get_claude().analyze(news, filter_result, matched_stocks)

                    # 뉴스 하나가 여러 종목에 영향을 줄 수 있으므로(예: 분쟁 뉴스 -> 방산주 여러 개)
                    # 종목별 평가를 각각 검증하고 각각 출력한다.
                    for assessment in claude_result.assessments:
                        if assessment.ticker and self.stock_matcher.available:
                            row = self.stock_matcher.master.find_by_ticker(assessment.ticker)
                            assessment.ticker_verified = row is not None
                            if row is None:
                                logger.warning(
                                    "Claude가 제시한 티커(%s)가 실제 상장 목록에 없어 watch로 강등: %s",
                                    assessment.ticker,
                                    news.title,
                                )
                                assessment.recommended_action = RecommendedAction.WATCH

                    record.claude_result = claude_result

                    for assessment in claude_result.assessments:
                        if assessment.recommended_action == RecommendedAction.BUY_CANDIDATE:
                            console.print(
                                f"[bold green]★ 매수 후보 (Claude 확정)[/bold green] "
                                f"{assessment.company_name or '?'}"
                                f"({assessment.ticker or '?'}) "
                                f"confidence={assessment.confidence:.2f}\n"
                                f"  뉴스: {news.title}\n"
                                f"  근거: {assessment.reasoning}\n"
                                f"  URL: {news.url}"
                            )
                except RuntimeError as e:
                    # ANTHROPIC_API_KEY 미설정 등 - 파이프라인 전체를 죽이지 않고 스킵
                    logger.error("Claude 분석 스킵: %s", e)
            elif filter_result.sentiment == Sentiment.POSITIVE:
                # Claude 비활성화 상태 - 1차 필터 + 종목 실재 검증만으로 후보를 표시한다.
                # Claude 정밀분석이 없으므로 신뢰도(confidence)는 여전히 검증되지 않은 상태임을 명시.
                stocks_desc = (
                    ", ".join(f"{m.matched_name}({m.ticker})" for m in matched_stocks)
                    if matched_stocks
                    else "미검증"
                )
                console.print(
                    f"[bold yellow]☆ 호재 후보 (1차 필터, 종목검증={stocks_desc})[/bold yellow]\n"
                    f"  뉴스: {news.title}\n"
                    f"  근거: {filter_result.reason}\n"
                    f"  URL: {news.url}"
                )

            self.store.save(record)
            records.append(record)

        return records

    def close(self) -> None:
        self.store.close()
