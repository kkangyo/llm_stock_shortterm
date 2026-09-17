"""RSS 기반 뉴스 수집기.

config.yaml 의 news_sources.rss_feeds 에 등록된 피드를 주기적으로 읽어
아직 처리하지 않은 신규 기사만 NewsItem 으로 반환한다.
"""
from __future__ import annotations

import hashlib
import logging
from collections import deque
from datetime import datetime

import feedparser
import requests

from stock_news_bot.config import settings
from stock_news_bot.models.schemas import NewsItem

logger = logging.getLogger(__name__)

# 일부 언론사(예: 한국경제)는 User-Agent가 없는 요청에 깨진/축약된 응답을 주므로
# feedparser.parse(url) 대신 requests로 직접 받아서 넘긴다.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_REQUEST_TIMEOUT = 10


def _make_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _parse_published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6])
    return None


class NewsCollector:
    """여러 RSS 피드를 폴링하며 중복 없이 신규 뉴스만 내보낸다."""

    def __init__(self, feeds: list[dict[str, str]] | None = None):
        self.feeds = feeds if feeds is not None else settings.rss_feeds
        self._seen_ids: deque[str] = deque(maxlen=settings.dedup_cache_size)
        self._seen_set: set[str] = set()

    def _mark_seen(self, news_id: str) -> None:
        if len(self._seen_ids) == self._seen_ids.maxlen:
            oldest = self._seen_ids.popleft()
            self._seen_set.discard(oldest)
        self._seen_ids.append(news_id)
        self._seen_set.add(news_id)

    def fetch_new(self) -> list[NewsItem]:
        """모든 피드를 조회해서 아직 못 본 뉴스만 반환한다."""
        new_items: list[NewsItem] = []

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
                if news_id in self._seen_set:
                    continue

                self._mark_seen(news_id)
                new_items.append(
                    NewsItem(
                        id=news_id,
                        title=entry.get("title", "").strip(),
                        summary=entry.get("summary", "").strip(),
                        url=link,
                        source=name,
                        published_at=_parse_published(entry),
                    )
                )

        if new_items:
            logger.info("신규 뉴스 %d건 수집", len(new_items))
        return new_items
