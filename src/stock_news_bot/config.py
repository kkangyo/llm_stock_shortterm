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

        # claude
        cc = self._raw["claude"]
        self.claude_enabled: bool = cc.get("enabled", False)
        self.claude_model: str = cc["model"]
        self.claude_max_tokens: int = cc["max_tokens"]
        self.claude_min_confidence: float = cc["min_confidence"]
        self.anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")

        # storage
        self.sqlite_path: Path = PROJECT_ROOT / self._raw["storage"]["sqlite_path"]

        # logging
        lg = self._raw["logging"]
        self.log_level: str = lg["level"]
        self.log_file: Path = PROJECT_ROOT / lg["file"]


settings = Settings()
