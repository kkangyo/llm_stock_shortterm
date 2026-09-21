"""Ollama가 뉴스에서 추출한 종목명 후보를 실제 KOSPI/KOSDAQ 상장 종목과 대조한다.

정확도 우선순으로 세 단계를 거친다:
1. 정규화 후 완전 일치 (유일하게 매칭될 때만 채택)
2. 정규화 후 부분 문자열 포함 (뉴스 표기 변형 대응, 역시 유일할 때만 채택)
3. 유사도(difflib) 기반 근사 매칭

어느 단계든 여러 종목이 동시에 걸리면(중복 상호명, 애매한 부분일치 등) 오탐을
피하기 위해 매칭 실패로 처리한다.
"""
from __future__ import annotations

import difflib
import logging

from stock_news_bot.config import settings
from stock_news_bot.matcher.krx_master import KrxMaster, normalize_name
from stock_news_bot.models.schemas import Market, StockMatch

logger = logging.getLogger(__name__)

_FUZZY_CUTOFF = 0.85
_MIN_NORM_LEN_FOR_SUBSTRING = 2  # 너무 짧은 이름의 부분일치는 오탐이 잦아 제외
# 부분일치 두 문자열의 길이 차이가 너무 크면(예: "이닉스" vs "에스케이하이닉스")
# 우연히 짧은 이름이 긴 후보명 속에 끼어 있을 뿐인 오탐일 확률이 높아 제외한다.
_MIN_SUBSTRING_LEN_RATIO = 0.5


class StockMatcher:
    def __init__(self, master: KrxMaster | None = None, aliases: dict[str, str] | None = None):
        self.master = master or KrxMaster()
        # 별칭은 정규화된 이름 기준으로 조회한다 (뉴스 표기 변형에 안정적으로 대응).
        raw_aliases = aliases if aliases is not None else settings.stock_aliases
        self._aliases = {normalize_name(k): v for k, v in raw_aliases.items()}

    @property
    def available(self) -> bool:
        return self.master.available

    def match(self, candidate_name: str) -> StockMatch | None:
        if not candidate_name or not self.master.available:
            return None

        norm = normalize_name(candidate_name)
        if not norm:
            return None

        alias_target = self._aliases.get(norm)
        if alias_target is not None:
            alias_hits = self.master.find_exact(alias_target)
            if len(alias_hits) == 1:
                return self._to_match(candidate_name, alias_hits[0], "alias", 1.0)
            logger.warning(
                "별칭 '%s' -> '%s' 설정이 KRX 목록과 일치하지 않습니다 (0개 또는 중복). 설정을 확인하세요.",
                candidate_name,
                alias_target,
            )

        exact = self.master.find_exact(candidate_name)
        if len(exact) == 1:
            return self._to_match(candidate_name, exact[0], "exact", 1.0)
        if len(exact) > 1:
            logger.debug("종목명 '%s'가 여러 종목과 동시에 일치 (중복 상호명 가능성) - 매칭 보류", candidate_name)
            return None

        substring_rows = {
            row.ticker: row
            for norm_name, rows in self.master.by_norm_name.items()
            if len(norm_name) >= _MIN_NORM_LEN_FOR_SUBSTRING
            and (norm in norm_name or norm_name in norm)
            and min(len(norm), len(norm_name)) / max(len(norm), len(norm_name)) >= _MIN_SUBSTRING_LEN_RATIO
            for row in rows
        }
        if len(substring_rows) == 1:
            row = next(iter(substring_rows.values()))
            return self._to_match(candidate_name, row, "substring", 0.9)
        if len(substring_rows) > 1:
            logger.debug("종목명 '%s' 부분일치가 여러 종목에 걸림 - 매칭 보류", candidate_name)
            return None

        all_norm_names = list(self.master.by_norm_name.keys())
        close = difflib.get_close_matches(norm, all_norm_names, n=2, cutoff=_FUZZY_CUTOFF)
        if len(close) == 1:
            row = self.master.by_norm_name[close[0]][0]
            score = round(difflib.SequenceMatcher(None, norm, close[0]).ratio(), 3)
            return self._to_match(candidate_name, row, "fuzzy", score)

        return None

    def match_many(self, candidate_names: list[str]) -> list[StockMatch]:
        """후보명 목록을 매칭하고, 같은 종목이 중복 매칭되면 한 번만 남긴다."""
        matches: list[StockMatch] = []
        seen_tickers: set[str] = set()
        for name in candidate_names:
            m = self.match(name)
            if m and m.ticker not in seen_tickers:
                matches.append(m)
                seen_tickers.add(m.ticker)
        return matches

    @staticmethod
    def _to_match(candidate_name: str, row, method: str, score: float) -> StockMatch:
        return StockMatch(
            input_name=candidate_name,
            matched_name=row.name,
            ticker=row.ticker,
            market=Market(row.market),
            method=method,
            score=score,
        )
