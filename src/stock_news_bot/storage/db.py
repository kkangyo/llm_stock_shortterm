"""처리 결과를 SQLite에 기록한다 (추후 백테스트/분석용)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from stock_news_bot.models.schemas import PipelineRecord

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

    def close(self) -> None:
        self.conn.close()
