"""RSS 기반 뉴스 수집기.

config.yaml 의 news_sources.rss_feeds 에 등록된 피드를 주기적으로 읽어
아직 처리하지 않았고, 발행된 지 max_age_minutes 이내인 신규 기사만
NewsItem 으로 반환한다. 오래된 뉴스는 단타 매매 목적에 맞지 않아 걸러낸다.
"""
from __future__ import annotations

import hashlib
import logging
from collections import deque
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

    def __init__(self, feeds: list[dict[str, str]] | None = None):
        self.feeds = feeds if feeds is not None else settings.rss_feeds
        self.max_age = timedelta(minutes=settings.news_max_age_minutes)
        # 런타임 중 신규 수집분을 추적하는 롤링 윈도우 (dedup_cache_size로 개수 제한).
        # 재시작 시점의 이력은 여기 포함되지 않으므로 preload_seen()으로 별도 등록한다.
        self._seen_ids: deque[str] = deque(maxlen=settings.dedup_cache_size)
        self._seen_set: set[str] = set()

    def _mark_seen(self, news_id: str) -> None:
        if len(self._seen_ids) == self._seen_ids.maxlen:
            oldest = self._seen_ids.popleft()
            self._seen_set.discard(oldest)
        self._seen_ids.append(news_id)
        self._seen_set.add(news_id)

    def preload_seen(self, news_ids) -> None:
        """DB 등 외부에서 이미 처리된 것으로 확인된 news_id를 스킵 대상으로 등록한다.

        런타임 중 신규 수집분을 추적하는 `_seen_ids`(dedup_cache_size로 제한된 롤링 윈도우)와
        달리 여기서 추가된 id는 개수 제한 없이 계속 유지된다 - 재시작 시점에 이미 처리한
        뉴스를 다시 "신규"로 오인하지 않기 위한 것이라, 이후 새로 들어오는 뉴스 때문에
        밀려나서 제외 대상에서 빠지면 안 되기 때문이다.
        """
        self._seen_set.update(news_ids)

    def fetch_new(self) -> list[NewsItem]:
        """모든 피드를 조회해서, 아직 못 봤고 max_age_minutes 이내에 발행된
        뉴스만 최신순으로 정렬해서 반환한다."""
        now = utcnow()
        cutoff = now - self.max_age

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
                if news_id in self._seen_set:
                    continue

                published_at = _parse_published(entry)
                if published_at is not None and published_at < cutoff:
                    skipped_old += 1
                    continue

                self._mark_seen(news_id)
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
