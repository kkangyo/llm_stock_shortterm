"""Claude 2차 정밀분석: Ollama가 걸러낸 후보에 대해서만 호출한다.

tool use(함수 호출) 방식으로 강제해서 파싱 가능한 구조화된 JSON을 받는다.
프롬프트/유저메시지 조립은 llm/analysis_prompts.py에서 Gemini 백엔드와 공유한다.
"""
from __future__ import annotations

import logging

import anthropic

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

_STOCK_ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {
            "type": ["string", "null"],
            "description": "종목 코드",
        },
        "company_name": {
            "type": ["string", "null"],
            "description": "종목명",
        },
        "sentiment": {
            "type": "string",
            "enum": ["positive", "negative", "neutral"],
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "이 종목에 대한 판단의 확신도 (0~1)",
        },
        "is_already_priced_in": {
            "type": ["boolean", "null"],
            "description": "이미 주가에 반영됐을 가능성이 높은지",
        },
        "reasoning": {
            "type": "string",
            "description": "이 종목이 영향받는다고 본 판단 근거 (2~3문장)",
        },
        "recommended_action": {
            "type": "string",
            "enum": ["buy_candidate", "watch", "ignore"],
        },
    },
    "required": ["sentiment", "confidence", "reasoning", "recommended_action"],
}

_TOOL_SCHEMA = {
    "name": "report_analysis",
    "description": "뉴스에 대한 정밀 분석 결과를, 영향받는 종목별로 구조화된 형태로 보고한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "affected_stocks": {
                "type": "array",
                "description": (
                    "이 뉴스로 영향받는다고 판단되는 종목 목록. 여러 종목이 동시에 "
                    "영향받을 수 있다. 특정할 수 있는 종목이 없으면 빈 배열."
                ),
                "items": _STOCK_ASSESSMENT_SCHEMA,
            },
        },
        "required": ["affected_stocks"],
    },
}


class ClaudeAnalyzer:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        key = api_key or settings.anthropic_api_key
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."
            )
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model or settings.claude_model

    def analyze(
        self,
        news: NewsItem,
        ollama_result: OllamaFilterResult,
        matched_stocks: list[StockMatch] | None = None,
    ) -> ClaudeAnalysisResult:
        user_content = build_user_content(news, ollama_result, matched_stocks)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=settings.claude_max_tokens,
                system=ANALYSIS_SYSTEM_PROMPT,
                tools=[_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "report_analysis"},
                messages=[{"role": "user", "content": user_content}],
            )
            tool_use = next(
                block for block in response.content if block.type == "tool_use"
            )
            data = tool_use.input

            assessments = [
                StockAssessment(
                    ticker=item.get("ticker"),
                    company_name=item.get("company_name"),
                    sentiment=Sentiment(item["sentiment"]),
                    confidence=float(item["confidence"]),
                    is_already_priced_in=item.get("is_already_priced_in"),
                    reasoning=item["reasoning"],
                    recommended_action=RecommendedAction(item["recommended_action"]),
                )
                for item in data.get("affected_stocks", [])
            ]
            return ClaudeAnalysisResult(news_id=news.id, assessments=assessments)
        except Exception:
            logger.exception("Claude 분석 실패 (news_id=%s)", news.id)
            return ClaudeAnalysisResult(
                news_id=news.id,
                assessments=[
                    StockAssessment(
                        sentiment=Sentiment.NEUTRAL,
                        confidence=0.0,
                        reasoning="claude 호출 실패",
                        recommended_action=RecommendedAction.IGNORE,
                    )
                ],
            )
