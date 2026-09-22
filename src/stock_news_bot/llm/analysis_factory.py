"""config.yaml의 analysis.backend 값에 따라 2차 정밀분석 백엔드를 생성한다."""
from __future__ import annotations

from stock_news_bot.config import settings
from stock_news_bot.llm.analysis_base import AnalysisBackend


def create_analyzer() -> AnalysisBackend:
    backend = settings.analysis_backend

    if backend == "claude":
        from stock_news_bot.llm.claude_client import ClaudeAnalyzer

        return ClaudeAnalyzer()

    if backend == "gemini":
        from stock_news_bot.llm.gemini_client import GeminiAnalyzer

        return GeminiAnalyzer()

    raise ValueError(
        f"알 수 없는 analysis.backend: {backend!r} - 'claude' 또는 'gemini'여야 합니다."
    )
