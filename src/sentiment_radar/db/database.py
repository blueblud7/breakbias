"""SQLite 접근 계층 — 스키마 초기화, 아이템 upsert, 조회."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..config import db_path
from ..models import Item
from ..utils.text import content_hash, normalize_url

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    """얇은 SQLite 래퍼. context manager 로 사용."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else db_path()
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA journal_mode = WAL;")

    # --- lifecycle ---
    def init_schema(self) -> None:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        self.conn.executescript(sql)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- items ---
    def insert_item(self, item: Item) -> int | None:
        """아이템 저장. (theme, url_normalized) 중복 시 무시하고 None 반환."""
        if not item.url_normalized:
            item.url_normalized = normalize_url(item.url)
        if not item.content_hash:
            item.content_hash = content_hash(item.title, item.url)

        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO items (
                theme, source_type, source_name, title, content_snippet,
                url, url_normalized, author, published_at, collected_at,
                reach_score, lang, keyword_matched, content_hash
            ) VALUES (
                :theme, :source_type, :source_name, :title, :content_snippet,
                :url, :url_normalized, :author, :published_at, :collected_at,
                :reach_score, :lang, :keyword_matched, :content_hash
            )
            """,
            item.to_row(),
        )
        self.conn.commit()
        # rowcount 0 이면 중복으로 무시된 것
        return cur.lastrowid if cur.rowcount else None

    def insert_items(self, items: Iterable[Item]) -> int:
        """여러 아이템 저장. 실제 삽입된 건수 반환."""
        inserted = 0
        for it in items:
            if self.insert_item(it) is not None:
                inserted += 1
        return inserted

    def recent_hashes(self, theme: str, lookback_days: int) -> set[str]:
        """최근 N일 아이템의 content_hash 집합 (dedup 비교용)."""
        rows = self.conn.execute(
            """
            SELECT content_hash FROM items
            WHERE theme = ?
              AND collected_at >= datetime('now', ?)
            """,
            (theme, f"-{int(lookback_days)} days"),
        ).fetchall()
        return {r["content_hash"] for r in rows if r["content_hash"]}

    def count_items(self, theme: str | None = None) -> int:
        if theme:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM items WHERE theme = ?", (theme,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
        return int(row["n"])

    def fetch_items(
        self, theme: str, limit: int = 50
    ) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT * FROM items WHERE theme = ?
            ORDER BY collected_at DESC LIMIT ?
            """,
            (theme, limit),
        ).fetchall()


def get_db(path: Path | str | None = None, *, init: bool = True) -> Database:
    """DB 핸들 획득 (필요 시 스키마 초기화)."""
    db = Database(path)
    if init:
        db.init_schema()
    return db
