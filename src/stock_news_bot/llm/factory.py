"""config.yaml의 local_filter.backend 값에 따라 1차 필터 백엔드를 생성한다."""
from __future__ import annotations

from stock_news_bot.config import settings
from stock_news_bot.llm.base import LocalFilterBackend


def create_local_filter() -> LocalFilterBackend:
    backend = settings.local_filter_backend

    if backend == "ollama":
        from stock_news_bot.llm.ollama_client import OllamaFilter

        return OllamaFilter()

    if backend == "openvino":
        # openvino-genai는 무겁고 선택적인 의존성이라 실제로 쓸 때만 임포트한다.
        from stock_news_bot.llm.openvino_client import OpenVinoFilter

        return OpenVinoFilter()

    raise ValueError(
        f"알 수 없는 local_filter.backend: {backend!r} - 'ollama' 또는 'openvino'여야 합니다."
    )
