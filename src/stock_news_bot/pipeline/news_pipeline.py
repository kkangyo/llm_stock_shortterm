"""뉴스 수집 -> 로컬 LLM 1차 필터(Ollama/OpenVINO) -> 2차 정밀분석(Claude/Gemini) 파이프라인.

주의: 이 모듈은 '호재 후보 탐지'까지만 수행한다. 실제 매수/매도 주문은
아직 구현하지 않았으며, 이후 증권사 API 연동 단계에서 별도로 추가한다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.logging import RichHandler

from stock_news_bot.collectors.article_fetcher import fetch_article_text
from stock_news_bot.collectors.news_collector import NewsCollector
from stock_news_bot.config import settings
from stock_news_bot.llm.analysis_base import AnalysisBackend
from stock_news_bot.llm.analysis_factory import create_analyzer
from stock_news_bot.llm.factory import create_local_filter
from stock_news_bot.matcher.krx_master import normalize_name
from stock_news_bot.matcher.stock_matcher import StockMatcher
from stock_news_bot.models.schemas import (
    PipelineRecord,
    RecommendedAction,
    Sentiment,
    StockAssessment,
    StockMatch,
)
from stock_news_bot.pipeline.ranking import score_assessment
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
        self.analyzer: AnalysisBackend | None = None

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

    def _get_analyzer(self) -> AnalysisBackend:
        # API 키가 없으면 여기서 바로 에러를 내도록 지연 초기화
        if self.analyzer is None:
            self.analyzer = create_analyzer()
        return self.analyzer

    def _match_stocks(self, news, filter_result):
        """LLM 후보 매칭 + 원문 직접 스캔을 합치되, "직접 언급" 주장은 원문으로 재검증한다.

        1차 필터는 candidate_tickers 중 테마 추정으로 뽑은 것만 thematic_tickers에 따로
        표시한다 (llm/prompts.py 참고). 즉 thematic_tickers에 없는 candidate_tickers
        항목은 LLM이 "본문에 직접 언급됨"이라고 주장하는 것이다.
        - 테마 추정 종목: 원문에 없는 게 당연하므로(그게 목적) KRX 매칭만 되면 그대로 채택.
        - 직접 언급 주장 종목: 실제로 원문에 그 이름이 있는지 확인해서, 없으면 할루시네이션
          (예: "삼화콘덴서"라고 해놓고 실제로는 "삼성전자"를 지어낸 경우)으로 보고 버린다.
        - 원문 직접 스캔(find_literal_mentions)은 위와 별개로 항상 추가 실행해서, LLM이
          아예 후보로 못 뽑았거나 이름을 잘못 재현한 진짜 언급 종목까지 보완한다.
        """
        text = f"{news.title} {news.summary}"
        text_norm = normalize_name(text)
        thematic_set = set(filter_result.thematic_tickers)

        matched_by_ticker = {}
        for name in filter_result.candidate_tickers:
            m = self.stock_matcher.match(name)
            if not m:
                continue
            if name in thematic_set:
                matched_by_ticker[m.ticker] = m
                continue
            # "직접 언급" 주장 - 원문에 그 이름 자체가 실제로 있는지 확인한다. 매칭된
            # 공식 상장명이 아니라 LLM이 준 원본 후보명으로 검사해야 네이버->NAVER 같은
            # 별칭 매칭까지 정상적으로 통과한다.
            if normalize_name(name) in text_norm:
                matched_by_ticker[m.ticker] = m
            else:
                logger.warning(
                    "LLM이 '%s'를 본문에 직접 언급됐다고 주장했지만 원문에 없어 제외"
                    " (할루시네이션 의심): %s",
                    name,
                    news.title,
                )

        literal_matches = self.stock_matcher.find_literal_mentions(text)
        for m in literal_matches:
            if m.ticker not in matched_by_ticker:
                matched_by_ticker[m.ticker] = m
                logger.info(
                    "[원문 직접 매칭] LLM 후보에는 없었지만 원문에서 발견: %s(%s)",
                    m.matched_name,
                    m.ticker,
                )

        return list(matched_by_ticker.values())

    def _reclassify_with_article_body(self, news):
        """1차 필터가 종목을 못 찾았을 때 기사 본문을 가져와 다시 판단해본다.

        RSS의 title/summary만으로는 정보가 부족한 경우가 많다(예: 요약이 비어있거나
        "○○○에 베팅했다" 같은 유료 기사 티저 제목). 실패한 뉴스에 한해서만 본문을
        가져오므로 폴링 주기 전체에 주는 부담은 크지 않다.

        (enriched_news, enriched_result) 튜플을 반환한다 - 본문을 못 가져왔으면 None.
        enriched_news를 같이 반환하는 건 _match_stocks()의 원문 직접 스캔이 (제목만이
        아니라) 방금 가져온 본문까지 대상으로 삼아야 하기 때문이다.
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
        return enriched_news, enriched_result

    def _log_priority_ranking(
        self,
        news,
        matched_stocks: list[StockMatch],
        new_candidates: list[StockAssessment],
    ) -> None:
        """buy_candidate가 여러 개 동시에 뜰 때(같은 뉴스든 다른 뉴스든) 우선순위를 매겨
        보여준다. 점수 계산 로직은 pipeline/ranking.py 참고 - 아직 이 순위로 실제 매매를
        자동 결정하지는 않고, 사람이 최종 하나를 고를 때 참고할 근거만 제공한다.

        비교 대상은 `ranking.window_minutes`(기본 30분) 안에 buy_candidate가 있었던
        뉴스들로 한정한다 - 오래된 후보가 계속 1위를 차지하는 걸 막기 위함.
        """
        # (점수, 종목명, 티커, confidence, 뉴스 제목, URL) 튜플로 모은다.
        pool: list[tuple[float, str, str | None, float, str, str]] = [
            (
                score_assessment(a, matched_stocks),
                a.company_name or a.ticker or "?",
                a.ticker,
                a.confidence,
                news.title,
                news.url,
            )
            for a in new_candidates
        ]

        since = utcnow() - timedelta(minutes=settings.ranking_window_minutes)
        for row in self.store.get_recent_buy_candidates(since):
            if row["news_id"] == news.id:
                continue  # 지금 처리 중인 뉴스는 위에서 이미 추가했음 (아직 DB엔 미저장)
            try:
                past_matched = [StockMatch.model_validate(m) for m in json.loads(row["matched_stocks"] or "[]")]
                past_assessments = [
                    StockAssessment.model_validate(a) for a in json.loads(row["claude_assessments"] or "[]")
                ]
            except (ValueError, TypeError):
                continue
            for a in past_assessments:
                if a.recommended_action != RecommendedAction.BUY_CANDIDATE:
                    continue
                pool.append(
                    (
                        score_assessment(a, past_matched),
                        a.company_name or a.ticker or "?",
                        a.ticker,
                        a.confidence,
                        row["title"],
                        row["url"],
                    )
                )

        pool.sort(key=lambda row: row[0], reverse=True)
        lines = [
            f"{i + 1}위 {name}({ticker or '?'}) score={score:.3f} confidence={conf:.2f} | {title}"
            for i, (score, name, ticker, conf, title, _url) in enumerate(pool)
        ]
        logger.info(
            "[우선순위:최근%d분,%d건] %s",
            settings.ranking_window_minutes,
            len(pool),
            " / ".join(lines),
        )
        if len(pool) > 1:
            top_score, top_name, top_ticker, *_ = pool[0]
            console.print(
                f"[bold cyan]▲ 현재 최우선 후보 (최근 {settings.ranking_window_minutes}분, "
                f"{len(pool)}건 중)[/bold cyan] {top_name}({top_ticker or '?'}) score={top_score:.3f}"
            )

    def run_once(self, limit: int | None = None) -> list[PipelineRecord]:
        """한 사이클 실행: 신규 뉴스 수집 -> 필터 -> 분석 -> 저장.

        limit을 주면 최신 뉴스 N건만 처리한다 (수동 테스트용).
        반환값은 이번 사이클에서 처리한 전체 레코드 (매수 후보 아닌 것 포함).
        """
        records: list[PipelineRecord] = []
        new_items = self.collector.fetch_new(limit=limit)

        for news in new_items:
            filter_result = self.local_filter.classify(news)
            record = PipelineRecord(news=news, ollama_result=filter_result)

            if not filter_result.is_relevant:
                logger.debug("1차 필터 통과 못함: %s", news.title)
                self.store.save(record)
                records.append(record)
                continue

            logger.info("[1차 통과] %s | %s", filter_result.sentiment.value, news.title)

            if filter_result.sentiment == Sentiment.NEGATIVE:
                # 이 시스템은 매수 후보를 찾는 게 목적이라 악재는 종목을 특정해도 쓸모가
                # 없다. 종목 매칭/본문 보강 재판단/Claude 2차 분석을 전부 생략해서 속도를
                # 높인다 (관련성/감성 판단까지만 하고 넘어감).
                logger.debug("악재로 판단 - 속도를 위해 종목 매칭/정밀분석 생략: %s", news.title)
                self.store.save(record)
                records.append(record)
                continue

            # 1차 필터가 뽑은 종목명 후보가 실제 코스피/코스닥 상장 종목인지 대조한다
            # (+ 원문 직접 스캔으로 LLM이 놓치거나 잘못 재현한 종목명도 보완).
            # 매칭에 실패하면(=실재하지 않는 종목/오인식) 이후 단계로 넘기지 않는다.
            matched_stocks = self._match_stocks(news, filter_result)
            record.matched_stocks = matched_stocks

            if self.stock_matcher.available and not matched_stocks:
                enriched = self._reclassify_with_article_body(news)
                if enriched and enriched[1].is_relevant:
                    news, filter_result = enriched
                    record.ollama_result = filter_result
                    matched_stocks = self._match_stocks(news, filter_result)
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

            if settings.analysis_enabled:
                try:
                    analysis_result = self._get_analyzer().analyze(news, filter_result, matched_stocks)

                    # 뉴스 하나가 여러 종목에 영향을 줄 수 있으므로(예: 분쟁 뉴스 -> 방산주 여러 개)
                    # 종목별 평가를 각각 검증하고 각각 출력한다.
                    for assessment in analysis_result.assessments:
                        if assessment.ticker and self.stock_matcher.available:
                            row = self.stock_matcher.master.find_by_ticker(assessment.ticker)
                            if row is None:
                                assessment.ticker_verified = False
                                logger.warning(
                                    "2차 분석(%s)이 제시한 티커(%s)가 실제 상장 목록에 없어 watch로 강등: %s",
                                    settings.analysis_backend,
                                    assessment.ticker,
                                    news.title,
                                )
                                assessment.recommended_action = RecommendedAction.WATCH
                            elif assessment.company_name and normalize_name(
                                row.name
                            ) != normalize_name(assessment.company_name):
                                # 티커 자체는 KRX에 실존하지만 그 티커가 가리키는 실제 회사가
                                # LLM이 말한 회사명과 다른 경우 - "존재하지 않는 티커"보다
                                # 훨씬 위험한 할루시네이션(엉뚱한 종목을 매수 후보로 올림).
                                # find_by_ticker()는 존재 여부만 보고 이름 일치는 안 보므로
                                # 여기서 별도로 대조해야 한다.
                                assessment.ticker_verified = False
                                logger.warning(
                                    "2차 분석(%s)이 제시한 티커(%s)가 회사명(%s)과 불일치(실제=%s)"
                                    " - watch로 강등: %s",
                                    settings.analysis_backend,
                                    assessment.ticker,
                                    assessment.company_name,
                                    row.name,
                                    news.title,
                                )
                                assessment.recommended_action = RecommendedAction.WATCH
                            else:
                                assessment.ticker_verified = True

                    record.claude_result = analysis_result

                    # 결과가 buy_candidate가 아니면(watch/ignore) 아래 루프가 아무 로그도
                    # 안 남겨서, 로그만 봐서는 2차 분석이 실제로 수행됐는지조차 알 수 없었다.
                    # watch/ignore로 끝나도 "분석은 했다"는 사실 자체를 항상 남긴다.
                    if analysis_result.assessments:
                        summary = ", ".join(
                            f"{a.company_name or a.ticker or '?'}"
                            f"({a.recommended_action.value}, confidence={a.confidence:.2f})"
                            for a in analysis_result.assessments
                        )
                    else:
                        summary = "영향받는 종목 없음"
                    logger.info(
                        "[2차 분석 완료:%s] %s | %s",
                        settings.analysis_backend,
                        summary,
                        news.title,
                    )

                    for assessment in analysis_result.assessments:
                        if assessment.recommended_action == RecommendedAction.BUY_CANDIDATE:
                            # console.print()는 rich Console에 직접 쓰기 때문에 logging
                            # 모듈을 거치지 않아 logs/pipeline.log 에는 안 남는다. 근거/URL을
                            # 로그 파일에도 남기려면 logger로 따로 기록해야 한다.
                            logger.info(
                                "[매수 후보] %s(%s) confidence=%.2f | 근거: %s | URL: %s",
                                assessment.company_name or "?",
                                assessment.ticker or "?",
                                assessment.confidence,
                                assessment.reasoning,
                                news.url,
                            )
                            console.print(
                                f"[bold green]★ 매수 후보 ({settings.analysis_backend} 확정)[/bold green] "
                                f"{assessment.company_name or '?'}"
                                f"({assessment.ticker or '?'}) "
                                f"confidence={assessment.confidence:.2f}\n"
                                f"  뉴스: {news.title}\n"
                                f"  근거: {assessment.reasoning}\n"
                                f"  URL: {news.url}"
                            )

                    buy_candidates_now = [
                        a
                        for a in analysis_result.assessments
                        if a.recommended_action == RecommendedAction.BUY_CANDIDATE
                    ]
                    if buy_candidates_now:
                        self._log_priority_ranking(news, matched_stocks, buy_candidates_now)
                except RuntimeError as e:
                    # API 키 미설정 등 - 파이프라인 전체를 죽이지 않고 스킵
                    logger.error("2차 분석(%s) 스킵: %s", settings.analysis_backend, e)
            elif filter_result.sentiment == Sentiment.POSITIVE:
                # 2차 분석 비활성화 상태 - 1차 필터 + 종목 실재 검증만으로 후보를 표시한다.
                # 2차 정밀분석이 없으므로 신뢰도(confidence)는 여전히 검증되지 않은 상태임을 명시.
                stocks_desc = (
                    ", ".join(f"{m.matched_name}({m.ticker})" for m in matched_stocks)
                    if matched_stocks
                    else "미검증"
                )
                logger.info(
                    "[호재 후보] 종목검증=%s | 근거: %s | URL: %s",
                    stocks_desc,
                    filter_result.reason,
                    news.url,
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
