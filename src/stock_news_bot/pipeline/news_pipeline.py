"""뉴스 수집 -> Ollama 1차 필터 -> Claude 2차 분석 파이프라인.

주의: 이 모듈은 '호재 후보 탐지'까지만 수행한다. 실제 매수/매도 주문은
아직 구현하지 않았으며, 이후 증권사 API 연동 단계에서 별도로 추가한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.logging import RichHandler

from stock_news_bot.collectors.news_collector import NewsCollector
from stock_news_bot.config import settings
from stock_news_bot.llm.claude_client import ClaudeAnalyzer
from stock_news_bot.llm.ollama_client import OllamaFilter
from stock_news_bot.models.schemas import PipelineRecord, RecommendedAction, Sentiment
from stock_news_bot.storage.db import ResultStore

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
        # 재시작 전 최근에 이미 처리한 뉴스는 SQLite 이력에서 불러와 중복
        # 제거 상태를 미리 채운다 - 그래야 재실행해도 같은 후보가 다시 뜨지 않는다.
        seen_ids = self.store.get_recent_seen(timedelta(minutes=settings.news_max_age_minutes))
        self.collector = NewsCollector(seen_ids=seen_ids)
        self.ollama_filter = OllamaFilter()
        self.claude_analyzer: ClaudeAnalyzer | None = None

    def _get_claude(self) -> ClaudeAnalyzer:
        # API 키가 없으면 여기서 바로 에러를 내도록 지연 초기화
        if self.claude_analyzer is None:
            self.claude_analyzer = ClaudeAnalyzer()
        return self.claude_analyzer

    def run_once(self) -> list[PipelineRecord]:
        """한 사이클 실행: 신규 뉴스 수집 -> 필터 -> 분석 -> 저장.

        반환값은 이번 사이클에서 처리한 전체 레코드 (매수 후보 아닌 것 포함).
        """
        records: list[PipelineRecord] = []
        new_items = self.collector.fetch_new()

        for news in new_items:
            ollama_result = self.ollama_filter.classify(news)
            record = PipelineRecord(news=news, ollama_result=ollama_result)

            if not ollama_result.is_relevant:
                logger.debug("1차 필터 통과 못함: %s", news.title)
                self.store.save(record)
                records.append(record)
                continue

            logger.info("[1차 통과] %s | %s", ollama_result.sentiment.value, news.title)

            if settings.claude_enabled:
                try:
                    claude_result = self._get_claude().analyze(news, ollama_result)
                    record.claude_result = claude_result

                    if claude_result.recommended_action == RecommendedAction.BUY_CANDIDATE:
                        console.print(
                            f"[bold green]★ 매수 후보 (Claude 확정)[/bold green] "
                            f"{claude_result.company_name or '?'}"
                            f"({claude_result.ticker or '?'}) "
                            f"confidence={claude_result.confidence:.2f}\n"
                            f"  뉴스: {news.title}\n"
                            f"  근거: {claude_result.reasoning}\n"
                            f"  URL: {news.url}"
                        )
                except RuntimeError as e:
                    # ANTHROPIC_API_KEY 미설정 등 - 파이프라인 전체를 죽이지 않고 스킵
                    logger.error("Claude 분석 스킵: %s", e)
            elif ollama_result.sentiment == Sentiment.POSITIVE:
                # Claude 비활성화 상태 - Ollama 1차 필터만으로 후보를 표시한다.
                # Claude 정밀분석이 없으므로 종목 특정/신뢰도가 검증되지 않은 상태임을 명시.
                console.print(
                    f"[bold yellow]☆ 호재 후보 (Ollama 1차 필터만, 미검증)[/bold yellow] "
                    f"후보종목={ollama_result.candidate_tickers}\n"
                    f"  뉴스: {news.title}\n"
                    f"  근거: {ollama_result.reason}\n"
                    f"  URL: {news.url}"
                )

            self.store.save(record)
            records.append(record)

        return records

    def close(self) -> None:
        self.store.close()
