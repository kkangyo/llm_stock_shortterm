"""파이프라인 전체에서 사용하는 데이터 모델"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from stock_news_bot.timeutil import utcnow


class NewsItem(BaseModel):
    """뉴스 수집기가 만들어내는 원본 뉴스 단위"""

    id: str  # url 기반 해시 등으로 생성하는 고유 id
    title: str
    summary: str = ""
    url: str
    source: str
    published_at: datetime | None = None  # UTC (feedparser 정규화 기준)
    fetched_at: datetime = Field(default_factory=utcnow)


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


class Market(str, Enum):
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"


class StockMatch(BaseModel):
    """뉴스에서 언급된 종목명 후보를 실제 KRX 상장 종목과 대조한 결과"""

    input_name: str  # Ollama가 추출한 원본 후보명
    matched_name: str  # 실제 상장 종목명
    ticker: str
    market: Market
    method: str  # "exact" | "substring" | "fuzzy"
    score: float = 1.0  # 매칭 확신도 (fuzzy일수록 1.0 미만)


class StockAssessment(BaseModel):
    """Claude가 판단한, 이 뉴스로 영향받는 개별 종목에 대한 평가.

    하나의 뉴스가 여러 종목에 영향을 줄 수 있으므로(예: 특정 국가의 분쟁 뉴스 ->
    여러 방산주가 동시에 영향받음) ClaudeAnalysisResult는 이 평가를 리스트로 담는다.
    """

    ticker: str | None = None
    company_name: str | None = None
    sentiment: Sentiment
    confidence: float = Field(ge=0.0, le=1.0)
    is_already_priced_in: bool | None = None
    reasoning: str
    recommended_action: RecommendedAction
    # 파이프라인이 ticker를 KRX 마스터 목록과 대조한 결과.
    # None = 검증을 시도하지 않음(마스터 목록 로딩 실패 등), False = 상장 목록에 없는 티커(할루시네이션 의심).
    ticker_verified: bool | None = None


class ClaudeAnalysisResult(BaseModel):
    """2차 정밀분석 (Claude) 결과 - 소수 후보에 대한 구조화된 판단.

    영향받는 종목이 없다고 판단되면 assessments는 빈 리스트가 된다.
    """

    news_id: str
    assessments: list[StockAssessment] = Field(default_factory=list)


class PipelineRecord(BaseModel):
    """뉴스 하나에 대한 전체 파이프라인 처리 결과 (저장용)"""

    news: NewsItem
    ollama_result: OllamaFilterResult | None = None
    matched_stocks: list[StockMatch] = Field(default_factory=list)
    claude_result: ClaudeAnalysisResult | None = None
    created_at: datetime = Field(default_factory=utcnow)  # UTC
