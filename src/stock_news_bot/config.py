"""설정 로딩: config/config.yaml + .env"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class Settings:
    def __init__(self, config_path: Path | None = None):
        config_path = config_path or PROJECT_ROOT / "config" / "config.yaml"
        self._raw = _load_yaml(config_path)

        self.root = PROJECT_ROOT

        # market hours
        mh = self._raw["market_hours"]
        self.market_timezone: str = mh["timezone"]
        self.market_start: str = mh["start"]
        self.market_end: str = mh["end"]
        self.weekdays_only: bool = mh["weekdays_only"]

        # polling
        self.polling_interval_seconds: int = self._raw["polling"]["interval_seconds"]

        # news sources
        ns = self._raw["news_sources"]
        self.rss_feeds: list[dict[str, str]] = ns["rss_feeds"]
        self.news_max_age_minutes: int = ns["max_age_minutes"]
        self.dedup_cache_size: int = ns["dedup_cache_size"]
        self.dedup_lookback_days: int = ns.get("dedup_lookback_days", 2)

        # local filter backend 선택 (ollama | openvino)
        lf = self._raw.get("local_filter", {})
        self.local_filter_backend: str = lf.get("backend", "ollama")

        # ollama
        oc = self._raw["ollama"]
        self.ollama_host: str = os.getenv("OLLAMA_HOST", oc["host"])
        self.ollama_model: str = oc["model"]
        self.ollama_timeout_seconds: int = oc["timeout_seconds"]

        # openvino (Intel GPU/NPU 가속 백엔드)
        ov = self._raw.get("openvino", {})
        self.openvino_device: str = ov.get("device", "GPU")
        self.openvino_model_path: Path = PROJECT_ROOT / ov.get(
            "model_path", "models/openvino/qwen2.5-7b-instruct-int4-ov"
        )
        self.openvino_max_new_tokens: int = ov.get("max_new_tokens", 300)

        # stock matcher
        sm = self._raw.get("stock_matcher", {})
        self.krx_refresh_hours: float = sm.get("refresh_hours", 24)
        self.stock_aliases: dict[str, str] = sm.get("aliases", {}) or {}

        # 2차 정밀분석 - 어떤 백엔드를 쓸지, 켤지 말지는 여기서 결정하고
        # (claude:/gemini: 섹션은) 백엔드별 세부 설정만 담는다. local_filter와 같은 구조.
        an = self._raw.get("analysis", {})
        self.analysis_enabled: bool = an.get("enabled", False)
        self.analysis_backend: str = an.get("backend", "claude")
        self.analysis_min_confidence: float = an.get("min_confidence", 0.6)

        # claude
        cc = self._raw.get("claude", {})
        self.claude_model: str = cc.get("model", "claude-sonnet-5")
        self.claude_max_tokens: int = cc.get("max_tokens", 1024)
        self.anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")

        # gemini (무료 티어 제공 - .env의 GEMINI_API_KEY 필요)
        gc = self._raw.get("gemini", {})
        self.gemini_model: str = gc.get("model", "gemini-2.5-flash")
        self.gemini_max_output_tokens: int = gc.get("max_output_tokens", 1024)
        self.gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")

        # ranking - buy_candidate가 여러 개 동시에 뜰 때 우선순위 비교용 시간창
        rk = self._raw.get("ranking", {})
        self.ranking_window_minutes: int = rk.get("window_minutes", 30)

        # storage
        self.sqlite_path: Path = PROJECT_ROOT / self._raw["storage"]["sqlite_path"]

        # logging
        lg = self._raw["logging"]
        self.log_level: str = lg["level"]
        self.log_file: Path = PROJECT_ROOT / lg["file"]


settings = Settings()
