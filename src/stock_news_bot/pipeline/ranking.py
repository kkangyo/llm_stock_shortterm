"""여러 buy_candidate가 동시에 나올 때 우선순위를 매기기 위한 점수 계산.

아직 시세/거래량 데이터가 파이프라인에 전혀 없어서(뉴스만 보는 구조), 유동성/모멘텀
같은 시장 데이터 기반 기준은 반영하지 못한다. 지금 있는 데이터(2차 분석 신뢰도, 선반영
여부, 종목 매칭 방식)만으로 점수를 매기는 1단계 구현이다 - 실제 매매를 자동으로
결정하지는 않고 순위를 보여주는 것까지만 한다.
"""
from __future__ import annotations

from stock_news_bot.models.schemas import StockAssessment, StockMatch

# 선반영됐을 가능성이 높다고 판단되면 단타 관점에서 먹을 게 없으므로 크게 감점.
# None(모름)은 확신이 안 서는 상태이므로 약하게만 감점.
_PRICED_IN_PENALTY = 0.3
_PRICED_IN_UNKNOWN_PENALTY = 0.85

# 2차 분석이 1차 필터의 매칭 목록을 넘어서 스스로 추가한 종목(테마 추정)에 대한 감점.
# 13번 항목(CLAUDE.md)에서 테마 추정 쪽 티커 할루시네이션 위험이 더 크다는 걸 실측으로
# 확인했음 - "직접 언급"보다 신뢰도를 낮게 잡는다.
_THEME_INFERRED_PENALTY = 0.7

# KRX 매칭이 fuzzy(유사도 매칭)였던 경우 - 애초에 종목명 매칭 자체가 애매했다는 뜻.
_FUZZY_MATCH_PENALTY = 0.9


def score_assessment(
    assessment: StockAssessment, matched_stocks: list[StockMatch]
) -> float:
    """assessment 하나의 우선순위 점수. 높을수록 매수 후보로 더 유력하다는 뜻."""
    score = assessment.confidence

    if assessment.is_already_priced_in is True:
        score *= _PRICED_IN_PENALTY
    elif assessment.is_already_priced_in is None:
        score *= _PRICED_IN_UNKNOWN_PENALTY

    match = next((m for m in matched_stocks if m.ticker == assessment.ticker), None)
    if match is None:
        score *= _THEME_INFERRED_PENALTY
    elif match.method == "fuzzy":
        score *= _FUZZY_MATCH_PENALTY

    return round(score, 4)
