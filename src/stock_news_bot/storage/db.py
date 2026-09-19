"""처리 결과를 SQLite에 기록한다 (추후 백테스트/분석용)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from stock_news_bot.models.schemas import PipelineRecord
from stock_news_bot.timeutil import utcnow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_records (
    news_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT,
    ollama_is_relevant INTEGER,
    ollama_sentiment TEXT,
    ollama_candidate_tickers TEXT,
    claude_ticker TEXT,
    claude_company_name TEXT,
    claude_sentiment TEXT,
    claude_confidence REAL,
    claude_recommended_action TEXT,
    claude_reasoning TEXT,
    created_at TEXT NOT NULL
);
"""


class ResultStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def save(self, record: PipelineRecord) -> None:
        o, c = record.ollama_result, record.claude_result
        self.conn.execute(
            """
            INSERT OR REPLACE INTO pipeline_records (
                news_id, title, url, source, published_at,
                ollama_is_relevant, ollama_sentiment, ollama_candidate_tickers,
                claude_ticker, claude_company_name, claude_sentiment,
                claude_confidence, claude_recommended_action, claude_reasoning,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.news.id,
                record.news.title,
                record.news.url,
                record.news.source,
                record.news.published_at.isoformat() if record.news.published_at else None,
                int(o.is_relevant) if o else None,
                o.sentiment.value if o else None,
                json.dumps(o.candidate_tickers, ensure_ascii=False) if o else None,
                c.ticker if c else None,
                c.company_name if c else None,
                c.sentiment.value if c else None,
                c.confidence if c else None,
                c.recommended_action.value if c else None,
                c.reasoning if c else None,
                record.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    def get_recent_seen(self, max_age: timedelta) -> dict[str, datetime]:
        """최근 max_age 이내에 처리한 뉴스의 news_id -> 발행시각(UTC) 맵.

        프로세스를 재시작해도 이미 처리한 뉴스를 다시 후보로 띄우지 않도록,
        파이프라인 시작 시 이 값으로 NewsCollector의 중복 제거 상태를 채운다.
        """
        cutoff = (utcnow() - max_age).isoformat()
        cur = self.conn.execute(
            """
            SELECT news_id, COALESCE(published_at, created_at)
            FROM pipeline_records
            WHERE COALESCE(published_at, created_at) >= ?
            """,
            (cutoff,),
        )
        return {news_id: datetime.fromisoformat(ts) for news_id, ts in cur.fetchall()}

    def close(self) -> None:
        self.conn.close()
