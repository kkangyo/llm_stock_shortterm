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
    StockAssessment,
    StockMatch,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
너는 한국 주식시장 뉴스 하나를 보고 영향받는 종목들에 대한 단기 매수 후보로서의 가치를
정밀 판단하는 애널리스트다. 아래 사항을 반드시 고려해서 report_analysis 도구를 호출해라.

- 뉴스 하나가 반드시 종목 하나만 가리키는 건 아니다. 예를 들어 특정 국가의 분쟁/전쟁 뉴스는
  여러 방산업체 종목에 동시에 영향을 줄 수 있고, 원자재 가격 급등 뉴스는 관련 소재/부품
  업체 여러 곳에 영향을 줄 수 있다. 이 뉴스로 영향받는 종목이 여러 개라고 판단되면
  affected_stocks 배열에 각 종목을 모두 담아라. 종목마다 영향 방향(sentiment)이나 강도가
  다를 수 있으니 각각 독립적으로 판단해라.
- 뉴스에서 실제로 어떤 종목(상장사)을 가리키는지 정확히 특정할 수 있는가
- 호재/악재의 강도와 신뢰도 (단순 루머/추측성 기사는 낮게 평가)
- 이미 시장에 널리 알려져 주가에 선반영됐을 가능성이 높은 뉴스인지
- 확신이 서지 않으면 confidence를 낮게, recommended_action을 watch 또는 ignore로 설정

[실제 상장 종목 매칭 결과]는 파이프라인이 1차 필터가 뽑은 후보를 KRX(코스피/코스닥) 전체
종목 목록과 대조해서 이미 검증해둔 값이다. 이 목록에 있는 종목은 그대로 활용하면 되고,
뉴스의 영향이 이 목록을 넘어선다고 판단되면(예: 특정 산업 테마 전반에 영향을 주는 사건이라
관련 종목이 더 있는 경우) 그 테마의 다른 실제 상장사를 스스로 추가해도 된다 - 단, 반드시
실존하는 정확한 상장사명이어야 한다. 새로 추가하는 종목의 ticker는 파이프라인이 이 응답
직후 다시 한 번 KRX 목록과 대조해서 검증하니, 확신 없는 이름은 넣지 마라. 특정할 수 있는
종목이 전혀 없다면 affected_stocks를 빈 배열로 둬라 (상장되지 않았거나 특정할 수 없는
종목을 임의로 지어내지 마라).

실제 매매는 이 시스템이 아직 수행하지 않는다. 너의 판단은 '매수 후보 리스트업'을 위한
참고자료로만 쓰인다는 점을 감안해 과감하게 추정하지 말고 보수적으로 판단해라.
"""

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
        if matched_stocks:
            matched_desc = "; ".join(
                f"{m.matched_name}({m.ticker}, {m.market.value}, 매칭방식={m.method})"
                for m in matched_stocks
            )
        else:
            matched_desc = "없음 (실제 상장 종목과 매칭되지 않음)"

        user_content = (
            f"제목: {news.title}\n"
            f"요약: {news.summary}\n"
            f"출처: {news.source}\n"
            f"URL: {news.url}\n\n"
            f"[1차 필터 결과] sentiment={ollama_result.sentiment.value}, "
            f"candidate_tickers={ollama_result.candidate_tickers}, "
            f"reason={ollama_result.reason}\n\n"
            f"[실제 상장 종목 매칭 결과] {matched_desc}"
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
