"""시간대 처리 헬퍼.

feedparser 의 published_parsed/updated_parsed 는 항상 UTC 기준
time.struct_time 으로 정규화되어 온다. 이 값과 비교/저장할 때는
로컬시간(datetime.now())을 섞어 쓰면 KST 기준 9시간 오차가 생기므로,
파이프라인 전체에서 "naive UTC"로 통일해서 다룬다.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
