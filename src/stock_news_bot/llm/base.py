"""1차 필터 백엔드가 구현해야 하는 공통 인터페이스.

Ollama(CPU)든 OpenVINO(Intel GPU/NPU)든 파이프라인 입장에서는 뉴스를 넣으면
OllamaFilterResult가 나오는 동일한 모양이면 되므로, 백엔드를 갈아끼울 수 있게
Protocol로만 계약을 명시한다.
"""
from __future__ import annotations

from typing import Protocol

from stock_news_bot.models.schemas import NewsItem, OllamaFilterResult


class LocalFilterBackend(Protocol):
    available: bool

    def classify(self, news: NewsItem) -> OllamaFilterResult: ...
