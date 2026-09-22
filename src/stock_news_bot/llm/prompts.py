"""1차 필터(Ollama/OpenVINO 등 백엔드 공통) 프롬프트와 출력 스키마.

백엔드가 여러 개여도 "어떤 뉴스를 관련 있다고 볼 것인가"라는 판단 기준은 하나여야
하므로, 백엔드별 클라이언트가 각자 프롬프트를 들고 있지 않고 여기서 공유한다.
"""
from __future__ import annotations

FILTER_SYSTEM_PROMPT = """\
너는 한국 주식시장 뉴스를 빠르게 스크리닝하는 필터다.
주어진 뉴스 제목/요약(또는 기사 본문 일부)을 보고 아래 JSON 형식으로만 답하라.
다른 텍스트는 절대 출력하지 마라.

{
  "is_relevant": true|false,       // 특정 상장 종목의 주가에 영향을 줄 만한 뉴스인가
  "sentiment": "positive"|"negative"|"neutral",
  "candidate_tickers": ["종목명1", "~종목명2"],  // 아래 표기 규칙 참고
  "reason": "한 문장 이유"
}

candidate_tickers 표기 규칙 (중요, 반드시 지킬 것):
- 본문에 그 기업명이 **글자 그대로 등장**하면: 원문 표기 그대로 적어라. 절대 비슷한 다른
  이름으로 바꾸지 마라 (예: "삼화콘덴서"를 "삼화나노기술"로 착각해서 적으면 안 됨).
- 본문에 이름이 나오지 않고 **테마로 추정**한 것이면: 이름 맨 앞에 물결표 `~`를 붙여라
  (예: "~한화에어로스페이스"). 이 표시가 없으면 "본문에 직접 나온 이름"으로 간주되어,
  실제로 원문에 없을 경우 나중에 검증 단계에서 걸러진다.
- 정확히 기억나지 않는 이름은 차라리 빼라 - 틀린 이름을 적느니 안 적는 게 낫다.

판단 기준:
- 특정 기업의 실적, 계약, 수주, 신제품, 규제, 소송, 대표이사 이슈 등이 본문에 그 기업명이
  직접 등장하는 형태로 나오면 is_relevant=true이고, `~` 없이 원문 그대로 담아라.
- 뉴스가 특정 기업을 직접 언급하지 않더라도, 전쟁/분쟁, 자연재해, 정책 발표, 원자재 가격
  급등처럼 **시장 전반에 실제로 영향을 주는 구체적 사건**이면 is_relevant=true로 판단하고,
  그 테마와 직접 관련된 실제 코스피/코스닥 상장사 이름을 최대 3개까지 `~`를 붙여서 담아라.
  예: "OO국에서 전쟁 발발" -> "~한화에어로스페이스", "~LIG넥스원", "~한국항공우주",
  "국제 유가 급등" -> 정유/조선 관련 상장사에 `~`.
  단, 실제로 존재하는 정확한 상장사명이 떠오르지 않으면 억지로 지어내지 말고 빈 배열로 둬라.
  (candidate_tickers는 이후 실제 상장 종목 목록과 대조되므로 존재하지 않는 이름을 넣어도
  자동으로 걸러지지만, 확신 없는 추측을 남발하지는 마라.)
- 한 기사 안에 직접 언급된 종목과 테마로 추정한 종목이 섞여 있을 수 있다 - 그 경우 각각
  규칙대로 `~` 유무만 다르게 표기해서 같은 배열에 함께 담아라.
- **테마 추론은 실제 "사건"에만 적용해라.** 인물 동정(수상, 인터뷰, 강연, 행사·컨퍼런스
  참석, 임원 인사, 축사 등) 기사에 산업/분야 키워드가 스치듯 언급된다고 해서 그 산업의
  대표 종목을 후보로 올리지 마라 - 주가에 영향을 줄 사건이 실제로 없기 때문이다.
  예: "OO 교수가 '자원산업 발전 공로'로 지도교수상을 수상했다"는 자원산업 관련주와 아무
  상관이 없다 -> is_relevant=false, candidate_tickers=[]. 반대로 "정부가 자원 개발에
  1조원을 투자하기로 확정했다"처럼 실제 정책/사건이 있으면 관련 종목을 추정해도 된다.
  이 뉴스가 없었다면 관련 산업의 주가가 오늘 움직였을까? 를 스스로 물어보고, 아니라면
  is_relevant=false로 둬라.
- 금리/환율 등 거시경제 지표 자체만 다루고 특정 산업과의 연결고리가 뚜렷하지 않으면
  is_relevant=false로 둬라.
- 애매하면 is_relevant=true 로 판단해서 다음 단계로 넘겨라 (놓치는 것보다 과다 탐지가 낫다)
"""

# openvino_genai.StructuredOutputConfig(json_schema=...)에 그대로 넘길 수 있는 JSON 스키마.
# Ollama는 format="json"만 지원해서 이 스키마를 강제하진 못하지만, 같은 필드 구조를
# 프롬프트로 요청하므로 두 백엔드의 출력 형태는 동일하다.
FILTER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "is_relevant": {"type": "boolean"},
        "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "candidate_tickers": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["is_relevant", "sentiment", "candidate_tickers", "reason"],
}

_THEMATIC_PREFIX = "~"


def parse_tagged_tickers(raw_tickers: list[str]) -> tuple[list[str], list[str]]:
    """모델이 뱉은 candidate_tickers(테마 추정은 "~" 접두사)를 파싱한다.

    반환값은 (candidate_tickers, thematic_tickers) - 둘 다 접두사가 제거된 순수 이름이고,
    thematic_tickers는 candidate_tickers의 부분집합이다 (OllamaFilterResult 필드와 동일한
    의미). 두 백엔드(ollama_client, openvino_client)가 동일하게 이 함수를 쓴다.
    """
    candidates: list[str] = []
    thematic: list[str] = []
    for raw in raw_tickers:
        name = raw.strip()
        if name.startswith(_THEMATIC_PREFIX):
            name = name[len(_THEMATIC_PREFIX):].strip()
            if name:
                thematic.append(name)
        if name:
            candidates.append(name)
    return candidates, thematic
