"""KOSPI/KOSDAQ 상장 종목 마스터 데이터 로딩 및 로컬 캐싱.

뉴스에서 언급된 종목명이 실제로 존재하는 상장 종목인지 검증하려면 기준이 되는
전체 종목 목록(티커/종목명/시장구분)이 필요하다. FinanceDataReader로 KRX 전체
목록을 받아와서 CSV로 캐싱하고, 설정된 주기(기본 24시간)마다만 재조회한다.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stock_news_bot.config import settings

logger = logging.getLogger(__name__)

# 뉴스 기사에서 종목명은 "삼성전자(주)", "㈜한화", "LG전자 보통주" 처럼
# 정식 상호 표기가 섞여 나오는 경우가 있어 비교 전에 제거한다.
_SUFFIX_PATTERN = re.compile(r"(주식회사|\(주\)|㈜|보통주|우선주)")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    name = _SUFFIX_PATTERN.sub("", name)
    name = _WHITESPACE_PATTERN.sub("", name)
    return name.strip().upper()


@dataclass(frozen=True)
class StockRow:
    ticker: str
    name: str
    market: str  # "KOSPI" | "KOSDAQ"


class KrxMaster:
    """KRX(코스피+코스닥) 상장 종목 전체 목록을 로컬 캐시로 관리한다."""

    def __init__(self, cache_path: Path | None = None, refresh_hours: float | None = None):
        self.cache_path = cache_path or (settings.root / "data" / "krx_master.csv")
        self.refresh_hours = (
            refresh_hours if refresh_hours is not None else settings.krx_refresh_hours
        )
        self._rows: list[StockRow] = []
        self._by_norm_name: dict[str, list[StockRow]] = {}
        self._by_ticker: dict[str, StockRow] = {}
        self._load()

    @property
    def available(self) -> bool:
        return bool(self._rows)

    @property
    def by_norm_name(self) -> dict[str, list[StockRow]]:
        return self._by_norm_name

    def find_exact(self, name: str) -> list[StockRow]:
        return self._by_norm_name.get(normalize_name(name), [])

    def find_by_ticker(self, ticker: str) -> StockRow | None:
        return self._by_ticker.get(ticker.strip().zfill(6))

    def all_rows(self) -> list[StockRow]:
        return self._rows

    def _cache_is_fresh(self) -> bool:
        if not self.cache_path.exists():
            return False
        age_hours = (time.time() - self.cache_path.stat().st_mtime) / 3600
        return age_hours < self.refresh_hours

    def _load(self) -> None:
        if self._cache_is_fresh() and self._load_from_cache():
            return
        if self._fetch_and_cache():
            return
        # 갱신 실패 - 캐시가 있으면(오래됐어도) 그대로 사용하는 게 검증 자체를 포기하는 것보다 낫다.
        if self._load_from_cache():
            logger.warning("KRX 종목 목록 갱신 실패 - 기존 캐시(%s)를 그대로 사용합니다.", self.cache_path)
        else:
            logger.warning(
                "KRX 종목 목록을 불러올 수 없습니다 (네트워크/캐시 모두 실패). "
                "종목 실재 여부 검증 없이 파이프라인을 계속 진행합니다."
            )

    def _load_from_cache(self) -> bool:
        try:
            df = pd.read_csv(self.cache_path, dtype=str)
            self._build_index(df)
            return True
        except Exception:
            logger.exception("KRX 캐시 파일 로딩 실패: %s", self.cache_path)
            return False

    def _fetch_and_cache(self) -> bool:
        try:
            import FinanceDataReader as fdr

            df = fdr.StockListing("KRX")
        except Exception:
            logger.exception("KRX 종목 목록 다운로드 실패")
            return False

        # FinanceDataReader는 코스닥 우량기업부 종목(알테오젠 등)을 "KOSDAQ"이 아니라
        # "KOSDAQ GLOBAL"로 별도 표기한다 - 어차피 코스닥 소속이므로 KOSDAQ으로 합친다.
        # (KONEX는 별개 시장이라 검증 대상에서 제외한다.)
        df = df[df["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])][["Code", "Name", "Market"]].copy()
        df["Market"] = df["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})

        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(self.cache_path, index=False, encoding="utf-8-sig")
        except Exception:
            logger.exception("KRX 캐시 저장 실패 (계속 진행)")

        self._build_index(df)
        logger.info("KRX 종목 목록 갱신 완료 - %d개 종목", len(self._rows))
        return True

    def _build_index(self, df: pd.DataFrame) -> None:
        rows = [
            StockRow(ticker=str(r["Code"]).strip().zfill(6), name=str(r["Name"]).strip(), market=str(r["Market"]).strip())
            for _, r in df.iterrows()
        ]
        by_norm: dict[str, list[StockRow]] = {}
        by_ticker: dict[str, StockRow] = {}
        for row in rows:
            by_norm.setdefault(normalize_name(row.name), []).append(row)
            by_ticker[row.ticker] = row
        self._rows = rows
        self._by_norm_name = by_norm
        self._by_ticker = by_ticker
