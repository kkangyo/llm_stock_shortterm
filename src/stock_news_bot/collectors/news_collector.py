"""RSS 기반 뉴스 수집기.

config.yaml 의 news_sources.rss_feeds 에 등록된 피드를 주기적으로 읽어
아직 처리하지 않았고, 발행된 지 max_age_minutes 이내인 신규 기사만
NewsItem 으로 반환한다. 오래된 뉴스는 단타 매매 목적에 맞지 않아
걸러내고, 남은 기사는 최신순으로 정렬해서 반환한다.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta

import feedparser
import requests

from stock_news_bot.config import settings
from stock_news_bot.models.schemas import NewsItem
from stock_news_bot.timeutil import utcnow

logger = logging.getLogger(__name__)

# 일부 언론사(예: 한국경제)는 User-Agent가 없는 요청에 깨진/축약된 응답을 주므로
# feedparser.parse(url) 대신 requests로 직접 받아서 넘긴다.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_REQUEST_TIMEOUT = 10


def _make_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _parse_published(entry) -> datetime | None:
    # feedparser 는 *_parsed 필드를 항상 UTC 기준 time.struct_time 으로 정규화한다.
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6])
    return None


class NewsCollector:
    """여러 RSS 피드를 폴링하며, 중복 없이 + 신선한 뉴스만 내보낸다."""

    def __init__(
        self,
        feeds: list[dict[str, str]] | None = None,
        seen_ids: dict[str, datetime] | None = None,
    ):
        self.feeds = feeds if feeds is not None else settings.rss_feeds
        self.max_age = timedelta(minutes=settings.news_max_age_minutes)
        # news_id -> 발행시각(UTC, naive). max_age 지나면 정리해서 메모리를 무한정
        # 늘리지 않는다. 시작 시 SQLite 이력으로 미리 채워서 재시작해도 최근에
        # 이미 처리한 뉴스를 다시 후보로 띄우지 않게 한다.
        self._seen: dict[str, datetime] = dict(seen_ids) if seen_ids else {}

    def _prune_seen(self, cutoff: datetime) -> None:
        stale = [nid for nid, seen_at in self._seen.items() if seen_at < cutoff]
        for nid in stale:
            del self._seen[nid]

    def fetch_new(self) -> list[NewsItem]:
        """모든 피드를 조회해서, 아직 못 봤고 max_age_minutes 이내에 발행된
        뉴스만 최신순으로 정렬해서 반환한다."""
        now = utcnow()
        cutoff = now - self.max_age
        self._prune_seen(cutoff)

        new_items: list[NewsItem] = []
        skipped_old = 0

        for feed in self.feeds:
            name, url = feed["name"], feed["url"]
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
            except Exception:
                logger.exception("RSS 조회 실패: %s (%s)", name, url)
                continue

            if parsed.bozo and not parsed.entries:
                logger.warning("RSS 파싱 이상: %s (%s) - %s", name, url, parsed.bozo_exception)
                continue

            for entry in parsed.entries:
                link = entry.get("link", "")
                if not link:
                    continue
                news_id = _make_id(link)
                if news_id in self._seen:
                    continue

                published_at = _parse_published(entry)
                if published_at is not None and published_at < cutoff:
                    skipped_old += 1
                    continue

                self._seen[news_id] = published_at or now
                new_items.append(
                    NewsItem(
                        id=news_id,
                        title=entry.get("title", "").strip(),
                        summary=entry.get("summary", "").strip(),
                        url=link,
                        source=name,
                        published_at=published_at,
                    )
                )

        # 발행시각을 모르는 기사는 우선순위를 가장 낮춰서 뒤로 보낸다.
        new_items.sort(key=lambda n: n.published_at or datetime.min, reverse=True)

        if new_items:
            logger.info(
                "신규 뉴스 %d건 수집 (최신순, %d분 이내)",
                len(new_items),
                settings.news_max_age_minutes,
            )
        if skipped_old:
            logger.debug(
                "오래된 뉴스 %d건 스킵 (max_age_minutes=%d)",
                skipped_old,
                settings.news_max_age_minutes,
            )

        return new_items
