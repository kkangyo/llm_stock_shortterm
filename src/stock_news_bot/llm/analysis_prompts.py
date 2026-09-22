"""2차 정밀분석(Claude/Gemini 등) 백엔드 공통 프롬프트와 유저 메시지 조립.

1차 필터와 마찬가지로, 백엔드가 여러 개여도 "이 뉴스를 어떻게 판단할 것인가"라는
기준은 하나여야 하므로 여기서 공유한다.
"""
from __future__ import annotations

from stock_news_bot.models.schemas import NewsItem, OllamaFilterResult, StockMatch

ANALYSIS_SYSTEM_PROMPT = """\
너는 한국 주식시장 뉴스 하나를 보고 영향받는 종목들에 대한 단기 매수 후보로서의 가치를
정밀 판단하는 애널리스트다. 아래 사항을 반드시 고려해서 구조화된 형태로 응답해라.

- 뉴스 하나가 반드시 종목 하나만 가리키는 건 아니다. 예를 들어 특정 국가의 분쟁/전쟁 뉴스는
  여러 방산업체 종목에 동시에 영향을 줄 수 있고, 원자재 가격 급등 뉴스는 관련 소재/부품
  업체 여러 곳에 영향을 줄 수 있다. 이 뉴스로 영향받는 종목이 여러 개라고 판단되면
  affected_stocks 배열에 각 종목을 모두 담아라. 종목마다 영향 방향(sentiment)이나 강도가
  다를 수 있으니 각각 독립적으로 판단해라.
- 단, 이런 테마 확장은 실제로 시장에 영향을 줄 만한 "사건"이 있을 때만 해라. 인물 동정
  (수상, 인터뷰, 강연, 행사 참석 등) 기사에 산업 키워드가 스치듯 언급된다고 해서 그 산업의
  종목을 끼워 넣지 마라 - "이 뉴스가 없었다면 오늘 그 종목들 주가가 움직였을까?"를 자문해서
  아니라면 affected_stocks에서 빼라.
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


def build_user_content(
    news: NewsItem,
    ollama_result: OllamaFilterResult,
    matched_stocks: list[StockMatch] | None,
) -> str:
    if matched_stocks:
        matched_desc = "; ".join(
            f"{m.matched_name}({m.ticker}, {m.market.value}, 매칭방식={m.method})"
            for m in matched_stocks
        )
    else:
        matched_desc = "없음 (실제 상장 종목과 매칭되지 않음)"

    return (
        f"제목: {news.title}\n"
        f"요약: {news.summary}\n"
        f"출처: {news.source}\n"
        f"URL: {news.url}\n\n"
        f"[1차 필터 결과] sentiment={ollama_result.sentiment.value}, "
        f"candidate_tickers={ollama_result.candidate_tickers}, "
        f"reason={ollama_result.reason}\n\n"
        f"[실제 상장 종목 매칭 결과] {matched_desc}"
    )


# Claude(JSON 스키마 dict)와 Gemini(Pydantic 모델) 양쪽 다 이 필드 구성과 반드시
# 일치시켜야 한다 - 하나만 고치고 다른 쪽을 안 고치면 두 백엔드의 판단 기준이 갈라진다.
STOCK_ASSESSMENT_FIELDS = {
    "ticker": "종목 코드 (알 수 없으면 null)",
    "company_name": "종목명 (알 수 없으면 null)",
    "sentiment": "positive | negative | neutral",
    "confidence": "이 종목에 대한 판단의 확신도 (0~1)",
    "is_already_priced_in": "이미 주가에 반영됐을 가능성이 높은지 (알 수 없으면 null)",
    "reasoning": "이 종목이 영향받는다고 본 판단 근거 (2~3문장)",
    "recommended_action": "buy_candidate | watch | ignore",
}
