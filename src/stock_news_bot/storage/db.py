"""처리 결과를 SQLite에 기록한다 (추후 백테스트/분석용)."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from stock_news_bot.models.schemas import PipelineRecord

logger = logging.getLogger(__name__)

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
    matched_stocks TEXT,
    claude_assessments TEXT,
    created_at TEXT NOT NULL
);
"""

# 기존에 만들어진 DB 파일에는 없을 수 있는 컬럼들 - CREATE TABLE IF NOT EXISTS는
# 이미 존재하는 테이블에 컬럼을 추가해주지 않으므로 별도로 마이그레이션한다.
_NEW_COLUMNS = {
    "matched_stocks": "TEXT",
    "claude_assessments": "TEXT",
}

# 뉴스 하나가 종목 하나만 가리킨다고 가정했던 구버전 스키마의 컬럼들.
# claude_assessments(종목별 리스트)로 대체됐으므로 있으면 제거한다.
_LEGACY_COLUMNS = [
    "claude_ticker",
    "claude_company_name",
    "claude_sentiment",
    "claude_confidence",
    "claude_recommended_action",
    "claude_reasoning",
    "claude_ticker_verified",
]


class ResultStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(pipeline_records)")}
        for column, col_type in _NEW_COLUMNS.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE pipeline_records ADD COLUMN {column} {col_type}")
        for column in _LEGACY_COLUMNS:
            if column in existing:
                try:
                    self.conn.execute(f"ALTER TABLE pipeline_records DROP COLUMN {column}")
                except sqlite3.OperationalError:
                    logger.warning("구버전 컬럼(%s) 제거 실패 (sqlite 버전이 낮을 수 있음) - 무시하고 계속", column)

    def get_recent_news_ids(self, since: datetime) -> set[str]:
        """since 이후 저장된 news_id 전체 - 재시작 시 dedup 시드로 쓰인다."""
        rows = self.conn.execute(
            "SELECT news_id FROM pipeline_records WHERE created_at >= ?", (since.isoformat(),)
        )
        return {row[0] for row in rows}

    def save(self, record: PipelineRecord) -> None:
        o, c = record.ollama_result, record.claude_result
        matched_json = json.dumps(
            [m.model_dump(mode="json") for m in record.matched_stocks], ensure_ascii=False
        )
        assessments_json = json.dumps(
            [a.model_dump(mode="json") for a in c.assessments] if c else [], ensure_ascii=False
        )
        self.conn.execute(
            """
            INSERT OR REPLACE INTO pipeline_records (
                news_id, title, url, source, published_at,
                ollama_is_relevant, ollama_sentiment, ollama_candidate_tickers,
                matched_stocks, claude_assessments,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                matched_json,
                assessments_json,
                record.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
