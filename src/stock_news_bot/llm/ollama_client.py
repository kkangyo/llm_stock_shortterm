"""Ollama 1차 필터: 대량 뉴스 중 '종목 관련 + 호재/악재' 후보만 빠르게 골라낸다.

정확도보다 재현율(recall)을 우선한다 - 애매하면 relevant=True 로 넘겨서
Claude 2차 분석이 최종 판단하게 한다.
"""
from __future__ import annotations

import json
import logging

import requests
from ollama import Client

from stock_news_bot.config import settings
from stock_news_bot.llm.prompts import FILTER_SYSTEM_PROMPT
from stock_news_bot.models.schemas import NewsItem, OllamaFilterResult, Sentiment

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = FILTER_SYSTEM_PROMPT


class OllamaFilter:
    def __init__(self, host: str | None = None, model: str | None = None):
        self.host = host or settings.ollama_host
        self.client = Client(host=self.host)
        self.model = model or settings.ollama_model
        self.available = self._check_available()
        if not self.available:
            logger.warning(
                "Ollama(%s)에 연결할 수 없습니다. 모든 뉴스를 1차 필터 통과 실패로 처리합니다. "
                "'ollama serve' 실행 여부를 확인하세요.",
                self.host,
            )

    def _check_available(self) -> bool:
        try:
            requests.get(f"{self.host}/api/version", timeout=2)
            return True
        except requests.RequestException:
            return False

    def classify(self, news: NewsItem) -> OllamaFilterResult:
        if not self.available:
            return OllamaFilterResult(
                news_id=news.id,
                is_relevant=False,
                sentiment=Sentiment.NEUTRAL,
                reason="ollama 연결 불가",
            )

        user_content = f"제목: {news.title}\n요약: {news.summary}"

        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                format="json",
                options={"temperature": 0},
            )
            content = response["message"]["content"]
            data = json.loads(content)
            return OllamaFilterResult(
                news_id=news.id,
                is_relevant=bool(data.get("is_relevant", False)),
                sentiment=Sentiment(data.get("sentiment", "neutral")),
                candidate_tickers=data.get("candidate_tickers", []) or [],
                reason=data.get("reason", ""),
            )
        except ConnectionError:
            self.available = False
            logger.warning("Ollama 연결이 끊어졌습니다 (news_id=%s) - 이후 뉴스는 필터 통과 실패로 처리", news.id)
            return OllamaFilterResult(
                news_id=news.id,
                is_relevant=False,
                sentiment=Sentiment.NEUTRAL,
                reason="ollama 연결 끊김",
            )
        except Exception:
            logger.exception("Ollama 분류 실패 (news_id=%s) - 안전하게 relevant=False 처리", news.id)
            return OllamaFilterResult(
                news_id=news.id,
                is_relevant=False,
                sentiment=Sentiment.NEUTRAL,
                reason="ollama 호출 실패",
            )
