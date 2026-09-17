"""파이프라인 전체에서 사용하는 데이터 모델"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    """뉴스 수집기가 만들어내는 원본 뉴스 단위"""

    id: str  # url 기반 해시 등으로 생성하는 고유 id
    title: str
    summary: str = ""
    url: str
    source: str
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=datetime.now)


class Sentiment(str, Enum):
    POSITIVE = "positive"  # 호재
    NEGATIVE = "negative"  # 악재
    NEUTRAL = "neutral"    # 무관/중립


class OllamaFilterResult(BaseModel):
    """1차 필터 (Ollama) 결과 - 빠르고 저렴한 대량 스크리닝"""

    news_id: str
    is_relevant: bool  # 특정 종목과 관련된 유의미한 뉴스인가
    sentiment: Sentiment
    candidate_tickers: list[str] = Field(default_factory=list)  # 언급된 종목명/티커 후보
    reason: str = ""


class RecommendedAction(str, Enum):
    BUY_CANDIDATE = "buy_candidate"  # 매수 후보로 상정 (실매매는 아직 미구현)
    WATCH = "watch"  # 조금 더 지켜봄
    IGNORE = "ignore"  # 무시


class ClaudeAnalysisResult(BaseModel):
    """2차 정밀분석 (Claude) 결과 - 소수 후보에 대한 구조화된 판단"""

    news_id: str
    ticker: str | None = None
    company_name: str | None = None
    sentiment: Sentiment
    confidence: float = Field(ge=0.0, le=1.0)
    is_already_priced_in: bool | None = None
    reasoning: str
    recommended_action: RecommendedAction


class PipelineRecord(BaseModel):
    """뉴스 하나에 대한 전체 파이프라인 처리 결과 (저장용)"""

    news: NewsItem
    ollama_result: OllamaFilterResult | None = None
    claude_result: ClaudeAnalysisResult | None = None
    created_at: datetime = Field(default_factory=datetime.now)
