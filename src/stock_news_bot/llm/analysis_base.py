"""2차 정밀분석 백엔드(Claude/Gemini)가 구현해야 하는 공통 인터페이스."""
from __future__ import annotations

from typing import Protocol

from stock_news_bot.models.schemas import (
    ClaudeAnalysisResult,
    NewsItem,
    OllamaFilterResult,
    StockMatch,
)


class AnalysisBackend(Protocol):
    def analyze(
        self,
        news: NewsItem,
        ollama_result: OllamaFilterResult,
        matched_stocks: list[StockMatch] | None = None,
    ) -> ClaudeAnalysisResult: ...
