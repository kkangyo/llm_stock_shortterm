"""Gemini 2차 정밀분석 - Claude의 무료 대안 백엔드.

Gemini API는 무료 티어를 제공하지만(2.5 Flash 기준 분당 10회/일 500회, 2026년
기준) 무료 티어 데이터는 구글이 자사 제품 개선에 활용할 수 있다는 점을 감안할 것
(유료 티어는 이 조항이 없음). 유료로 전환하려면 GEMINI_API_KEY를 결제 연동된
프로젝트 키로 바꾸기만 하면 되고 코드 변경은 필요 없다.

Claude의 tool use 대신 response_schema(Pydantic)로 구조화된 출력을 강제한다.
프롬프트/유저메시지 조립은 llm/analysis_prompts.py에서 Claude 백엔드와 공유한다.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from stock_news_bot.config import settings
from stock_news_bot.llm.analysis_prompts import ANALYSIS_SYSTEM_PROMPT, build_user_content
from stock_news_bot.models.schemas import (
    ClaudeAnalysisResult,
    NewsItem,
    OllamaFilterResult,
    RecommendedAction,
    Sentiment,
    StockAssessment,
    StockMatch,
)

logger = logging.getLogger(__name__)


class _AssessmentSchema(BaseModel):
    """Gemini의 response_schema로 그대로 넘기는 pydantic 모델.

    필드 구성은 claude_client.py의 _STOCK_ASSESSMENT_SCHEMA와 반드시 일치시킬 것
    (llm/analysis_prompts.py의 STOCK_ASSESSMENT_FIELDS 참고).
    """

    ticker: str | None = Field(default=None, description="종목 코드 (알 수 없으면 null)")
    company_name: str | None = Field(default=None, description="종목명 (알 수 없으면 null)")
    sentiment: str = Field(description="positive | negative | neutral")
    confidence: float = Field(ge=0.0, le=1.0, description="이 종목에 대한 판단의 확신도")
    is_already_priced_in: bool | None = Field(default=None)
    reasoning: str = Field(description="이 종목이 영향받는다고 본 판단 근거 (2~3문장)")
    recommended_action: str = Field(description="buy_candidate | watch | ignore")


class GeminiAnalyzer:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        key = api_key or settings.gemini_api_key
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요. "
                "https://aistudio.google.com/apikey 에서 무료로 발급받을 수 있습니다."
            )
        from google import genai
        from google.genai import types

        # google-genai SDK는 기본적으로 재시도를 아예 안 한다(stop_after_attempt(1)).
        # 그래서 일시적인 429(할당량)/503(고수요) 오류도 바로 실패 처리되어 "gemini 호출
        # 실패"로 남고 있었다 - 실측(2026-09-22)으로 확인. 429/503/5xx에 지수 백오프
        # 재시도를 붙여서 일시적 장애는 스스로 넘어가도록 함.
        retry_options = types.HttpRetryOptions(
            attempts=3, initial_delay=2.0, max_delay=15.0, exp_base=2.0
        )
        self.client = genai.Client(
            api_key=key, http_options=types.HttpOptions(retry_options=retry_options)
        )
        self.model = model or settings.gemini_model

    def analyze(
        self,
        news: NewsItem,
        ollama_result: OllamaFilterResult,
        matched_stocks: list[StockMatch] | None = None,
    ) -> ClaudeAnalysisResult:
        from google.genai import types

        user_content = build_user_content(news, ollama_result, matched_stocks)

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=ANALYSIS_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=list[_AssessmentSchema],
                    max_output_tokens=settings.gemini_max_output_tokens,
                    temperature=0,
                    # tools를 안 쓰는데도 SDK가 "AFC is enabled..." 경고를 매번 찍는다 -
                    # function calling 자체를 안 쓴다고 명시해서 그 무해한 경고를 끈다.
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
            items: list[_AssessmentSchema] = response.parsed or []

            assessments = [
                StockAssessment(
                    ticker=item.ticker,
                    company_name=item.company_name,
                    sentiment=Sentiment(item.sentiment),
                    confidence=item.confidence,
                    is_already_priced_in=item.is_already_priced_in,
                    reasoning=item.reasoning,
                    recommended_action=RecommendedAction(item.recommended_action),
                )
                for item in items
            ]
            return ClaudeAnalysisResult(news_id=news.id, assessments=assessments)
        except Exception:
            logger.exception("Gemini 분석 실패 (news_id=%s)", news.id)
            return ClaudeAnalysisResult(
                news_id=news.id,
                assessments=[
                    StockAssessment(
                        sentiment=Sentiment.NEUTRAL,
                        confidence=0.0,
                        reasoning="gemini 호출 실패",
                        recommended_action=RecommendedAction.IGNORE,
                    )
                ],
            )
