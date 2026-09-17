"""Claude 2차 정밀분석: Ollama가 걸러낸 후보에 대해서만 호출한다.

tool use(함수 호출) 방식으로 강제해서 파싱 가능한 구조화된 JSON을 받는다.
"""
from __future__ import annotations

import logging

import anthropic

from stock_news_bot.config import settings
from stock_news_bot.models.schemas import (
    ClaudeAnalysisResult,
    NewsItem,
    OllamaFilterResult,
    RecommendedAction,
    Sentiment,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
너는 한국 주식시장 뉴스 하나를 보고 특정 종목에 대한 단기 매수 후보로서의 가치를 정밀 판단하는
애널리스트다. 아래 사항을 반드시 고려해서 report_analysis 도구를 호출해라.

- 뉴스에서 실제로 어떤 종목(상장사)을 가리키는지 정확히 특정할 수 있는가
- 호재/악재의 강도와 신뢰도 (단순 루머/추측성 기사는 낮게 평가)
- 이미 시장에 널리 알려져 주가에 선반영됐을 가능성이 높은 뉴스인지
- 확신이 서지 않으면 confidence를 낮게, recommended_action을 watch 또는 ignore로 설정

실제 매매는 이 시스템이 아직 수행하지 않는다. 너의 판단은 '매수 후보 리스트업'을 위한
참고자료로만 쓰인다는 점을 감안해 과감하게 추정하지 말고 보수적으로 판단해라.
"""

_TOOL_SCHEMA = {
    "name": "report_analysis",
    "description": "뉴스에 대한 정밀 분석 결과를 구조화된 형태로 보고한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {
                "type": ["string", "null"],
                "description": "종목 코드 (알 수 없으면 null)",
            },
            "company_name": {
                "type": ["string", "null"],
                "description": "종목명 (알 수 없으면 null)",
            },
            "sentiment": {
                "type": "string",
                "enum": ["positive", "negative", "neutral"],
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "이 판단에 대한 확신도 (0~1)",
            },
            "is_already_priced_in": {
                "type": ["boolean", "null"],
                "description": "이미 주가에 반영됐을 가능성이 높은지",
            },
            "reasoning": {
                "type": "string",
                "description": "판단 근거 (2~3문장)",
            },
            "recommended_action": {
                "type": "string",
                "enum": ["buy_candidate", "watch", "ignore"],
            },
        },
        "required": ["sentiment", "confidence", "reasoning", "recommended_action"],
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
        self, news: NewsItem, ollama_result: OllamaFilterResult
    ) -> ClaudeAnalysisResult:
        user_content = (
            f"제목: {news.title}\n"
            f"요약: {news.summary}\n"
            f"출처: {news.source}\n"
            f"URL: {news.url}\n\n"
            f"[1차 필터 결과] sentiment={ollama_result.sentiment.value}, "
            f"candidate_tickers={ollama_result.candidate_tickers}, "
            f"reason={ollama_result.reason}"
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=settings.claude_max_tokens,
                system=_SYSTEM_PROMPT,
                tools=[_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "report_analysis"},
                messages=[{"role": "user", "content": user_content}],
            )
            tool_use = next(
                block for block in response.content if block.type == "tool_use"
            )
            data = tool_use.input

            return ClaudeAnalysisResult(
                news_id=news.id,
                ticker=data.get("ticker"),
                company_name=data.get("company_name"),
                sentiment=Sentiment(data["sentiment"]),
                confidence=float(data["confidence"]),
                is_already_priced_in=data.get("is_already_priced_in"),
                reasoning=data["reasoning"],
                recommended_action=RecommendedAction(data["recommended_action"]),
            )
        except Exception:
            logger.exception("Claude 분석 실패 (news_id=%s)", news.id)
            return ClaudeAnalysisResult(
                news_id=news.id,
                sentiment=Sentiment.NEUTRAL,
                confidence=0.0,
                reasoning="claude 호출 실패",
                recommended_action=RecommendedAction.IGNORE,
            )
