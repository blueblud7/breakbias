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

    # --- classifications ---
    def fetch_unclassified(self, theme: str, limit: int = 500) -> list[sqlite3.Row]:
        """아직 분류되지 않은 아이템 (classifications 에 없는 것)."""
        return self.conn.execute(
            """
            SELECT i.* FROM items i
            LEFT JOIN classifications c ON c.item_id = i.id
            WHERE i.theme = ? AND c.id IS NULL
            ORDER BY i.collected_at ASC
            LIMIT ?
            """,
            (theme, limit),
        ).fetchall()

    def insert_classification(self, row: dict[str, Any]) -> int | None:
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO classifications (
                item_id, sentiment, confidence, one_line_summary, key_argument,
                time_horizon, is_opinion, model, classified_at
            ) VALUES (
                :item_id, :sentiment, :confidence, :one_line_summary, :key_argument,
                :time_horizon, :is_opinion, :model, :classified_at
            )
            """,
            row,
        )
        self.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def fetch_classified(self, theme: str) -> list[sqlite3.Row]:
        """분류 완료된 아이템 + 분류결과 조인 (집계 입력용)."""
        return self.conn.execute(
            """
            SELECT
                i.id, i.source_type, i.reach_score,
                i.published_at, i.collected_at,
                c.sentiment, c.confidence, c.is_opinion
            FROM items i
            JOIN classifications c ON c.item_id = i.id
            WHERE i.theme = ?
            """,
            (theme,),
        ).fetchall()

    # --- aggregates ---
    def upsert_aggregate(self, theme: str, bucket_date: str, scope: str,
                         metrics: dict[str, Any], computed_at: str) -> None:
        row = {
            "theme": theme, "bucket_date": bucket_date, "scope": scope,
            "computed_at": computed_at, **metrics,
        }
        self.conn.execute(
            """
            INSERT INTO daily_aggregates (
                theme, bucket_date, scope, n_items,
                pct_pos_raw, pct_neu_raw, pct_neg_raw, nsi_raw,
                pct_pos_wt, pct_neu_wt, pct_neg_wt, nsi_wt,
                extreme_flag, divergence, computed_at
            ) VALUES (
                :theme, :bucket_date, :scope, :n_items,
                :pct_pos_raw, :pct_neu_raw, :pct_neg_raw, :nsi_raw,
                :pct_pos_wt, :pct_neu_wt, :pct_neg_wt, :nsi_wt,
                :extreme_flag, :divergence, :computed_at
            )
            ON CONFLICT(theme, bucket_date, scope) DO UPDATE SET
                n_items=excluded.n_items,
                pct_pos_raw=excluded.pct_pos_raw, pct_neu_raw=excluded.pct_neu_raw,
                pct_neg_raw=excluded.pct_neg_raw, nsi_raw=excluded.nsi_raw,
                pct_pos_wt=excluded.pct_pos_wt, pct_neu_wt=excluded.pct_neu_wt,
                pct_neg_wt=excluded.pct_neg_wt, nsi_wt=excluded.nsi_wt,
                extreme_flag=excluded.extreme_flag, divergence=excluded.divergence,
                computed_at=excluded.computed_at
            """,
            row,
        )
        self.conn.commit()

    def fetch_aggregates(self, theme: str, scope: str = "all",
                         limit: int = 30) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT * FROM daily_aggregates
            WHERE theme = ? AND scope = ?
            ORDER BY bucket_date DESC LIMIT ?
            """,
            (theme, scope, limit),
        ).fetchall()

    def fetch_classified_detailed(self, theme: str) -> list[sqlite3.Row]:
        """집계/총평 입력용: 아이템 + 분류 상세 조인."""
        return self.conn.execute(
            """
            SELECT
                i.id, i.source_type, i.source_name, i.title, i.url,
                i.reach_score, i.published_at, i.collected_at,
                c.sentiment, c.confidence, c.is_opinion,
                c.one_line_summary, c.key_argument, c.time_horizon
            FROM items i
            JOIN classifications c ON c.item_id = i.id
            WHERE i.theme = ?
            ORDER BY i.published_at DESC
            """,
            (theme,),
        ).fetchall()

    def fetch_aggregate_one(self, theme: str, bucket_date: str,
                            scope: str = "all") -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT * FROM daily_aggregates
               WHERE theme = ? AND bucket_date = ? AND scope = ?""",
            (theme, bucket_date, scope),
        ).fetchone()

    # --- M4: commentary ---
    def upsert_commentary(self, *, theme: str, bucket_date: str, commentary: str,
                          counter_args: str, model: str, generated_at: str) -> None:
        self.conn.execute(
            """
            INSERT INTO daily_commentary (theme, bucket_date, commentary, counter_args, model, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme, bucket_date) DO UPDATE SET
                commentary = excluded.commentary, counter_args = excluded.counter_args,
                model = excluded.model, generated_at = excluded.generated_at
            """,
            (theme, bucket_date, commentary, counter_args, model, generated_at),
        )
        self.conn.commit()

    def fetch_commentary(self, theme: str, bucket_date: str | None = None) -> sqlite3.Row | None:
        if bucket_date:
            return self.conn.execute(
                "SELECT * FROM daily_commentary WHERE theme = ? AND bucket_date = ?",
                (theme, bucket_date),
            ).fetchone()
        return self.conn.execute(
            "SELECT * FROM daily_commentary WHERE theme = ? ORDER BY bucket_date DESC LIMIT 1",
            (theme,),
        ).fetchone()

    def fetch_price_series(self, symbol: str, limit: int = 90) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            """SELECT * FROM price_history WHERE symbol = ?
               ORDER BY bucket_date DESC LIMIT ?""",
            (symbol, limit),
        ).fetchall()
        return list(reversed(rows))

    def distinct_source_types(self, theme: str) -> list[str]:
        """daily_aggregates 에서 소스타입 스코프만 (all/institutional/retail 제외)."""
        rows = self.conn.execute(
            """SELECT DISTINCT scope FROM daily_aggregates
               WHERE theme = ? AND scope NOT IN ('all','institutional','retail')""",
            (theme,),
        ).fetchall()
        return [r["scope"] for r in rows]

    # --- cost log ---
    def add_cost_log(self, *, bucket_date: str, model: str, call_type: str,
                     n_calls: int, prompt_tokens: int, completion_tokens: int,
                     cost_usd: float) -> None:
        from ..models import utcnow_iso
        self.conn.execute(
            """
            INSERT INTO llm_cost_log (
                bucket_date, model, call_type, n_calls,
                prompt_tokens, completion_tokens, cost_usd, logged_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bucket_date, model, call_type, n_calls,
             prompt_tokens, completion_tokens, cost_usd, utcnow_iso()),
        )
        self.conn.commit()

    def today_cost_usd(self, bucket_date: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS s FROM llm_cost_log WHERE bucket_date = ?",
            (bucket_date,),
        ).fetchone()
        return float(row["s"])

    # --- M6: predictions ---
    def insert_prediction(self, row: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO predictions (
                theme, created_at, my_view, confidence, horizon_days, basis,
                target_symbol, entry_date, entry_close, resolve_date, outcome
            ) VALUES (
                :theme, :created_at, :my_view, :confidence, :horizon_days, :basis,
                :target_symbol, :entry_date, :entry_close, :resolve_date, 'pending'
            )
            """,
            row,
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def fetch_due_predictions(self, as_of_date: str) -> list[sqlite3.Row]:
        """만기 도래(resolve_date <= as_of) 하고 아직 미판정인 예측."""
        return self.conn.execute(
            """
            SELECT * FROM predictions
            WHERE outcome = 'pending' AND resolve_date <= ?
            ORDER BY resolve_date ASC
            """,
            (as_of_date,),
        ).fetchall()

    def resolve_prediction(self, pred_id: int, *, exit_close: float,
                           actual_return: float, outcome: str, brier: float,
                           resolved_at: str) -> None:
        self.conn.execute(
            """
            UPDATE predictions SET
                exit_close = ?, actual_return = ?, outcome = ?,
                brier = ?, resolved_at = ?
            WHERE id = ?
            """,
            (exit_close, actual_return, outcome, brier, resolved_at, pred_id),
        )
        self.conn.commit()

    def fetch_predictions(self, theme: str | None = None, *,
                          resolved_only: bool = False,
                          limit: int = 1000) -> list[sqlite3.Row]:
        q = "SELECT * FROM predictions"
        conds, params = [], []
        if theme:
            conds.append("theme = ?")
            params.append(theme)
        if resolved_only:
            conds.append("outcome != 'pending'")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(q, params).fetchall()

    def has_prediction_on(self, theme: str, entry_date: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM predictions WHERE theme = ? AND entry_date = ? LIMIT 1",
            (theme, entry_date),
        ).fetchone()
        return row is not None

    # --- M6: rules + rule_history ---
    def insert_rule(self, row: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO rules (
                theme, name, condition_json, action_text, active,
                created_at, updated_at
            ) VALUES (
                :theme, :name, :condition_json, :action_text, 1,
                :created_at, :updated_at
            )
            """,
            row,
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_rule(self, rule_id: int, *, condition_json: str | None = None,
                    action_text: str | None = None, name: str | None = None,
                    updated_at: str) -> None:
        cur = self.fetch_rule(rule_id)
        if cur is None:
            raise KeyError(f"rule {rule_id} 없음")
        self.conn.execute(
            """
            UPDATE rules SET
                name = ?, condition_json = ?, action_text = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name if name is not None else cur["name"],
                condition_json if condition_json is not None else cur["condition_json"],
                action_text if action_text is not None else cur["action_text"],
                updated_at, rule_id,
            ),
        )
        self.conn.commit()

    def set_rule_active(self, rule_id: int, active: bool, updated_at: str) -> None:
        self.conn.execute(
            "UPDATE rules SET active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, updated_at, rule_id),
        )
        self.conn.commit()

    def set_rule_triggered(self, rule_id: int, at: str) -> None:
        self.conn.execute(
            "UPDATE rules SET last_triggered_at = ? WHERE id = ?", (at, rule_id)
        )
        self.conn.commit()

    def fetch_rule(self, rule_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()

    def fetch_rules(self, *, active_only: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM rules"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY created_at ASC"
        return self.conn.execute(q).fetchall()

    def insert_rule_history(self, rule_id: int, change_type: str,
                            snapshot_json: str, changed_at: str) -> None:
        self.conn.execute(
            """
            INSERT INTO rule_history (rule_id, change_type, snapshot_json, changed_at)
            VALUES (?, ?, ?, ?)
            """,
            (rule_id, change_type, snapshot_json, changed_at),
        )
        self.conn.commit()

    def fetch_rule_history(self, rule_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM rule_history WHERE rule_id = ? ORDER BY changed_at ASC",
            (rule_id,),
        ).fetchall()

    # --- M3: attention (Google Trends 등) ---
    def upsert_attention(self, *, theme: str, bucket_date: str, metric: str,
                         keyword: str, value: float | None, collected_at: str) -> None:
        self.conn.execute(
            """
            INSERT INTO attention_metrics (theme, bucket_date, metric, keyword, value, collected_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme, bucket_date, metric, keyword) DO UPDATE SET
                value = excluded.value, collected_at = excluded.collected_at
            """,
            (theme, bucket_date, metric, keyword, value, collected_at),
        )
        self.conn.commit()

    def fetch_attention(self, theme: str, metric: str | None = None,
                        limit: int = 90) -> list[sqlite3.Row]:
        if metric:
            return self.conn.execute(
                """SELECT * FROM attention_metrics WHERE theme = ? AND metric = ?
                   ORDER BY bucket_date DESC LIMIT ?""",
                (theme, metric, limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM attention_metrics WHERE theme = ? ORDER BY bucket_date DESC LIMIT ?",
            (theme, limit),
        ).fetchall()

    # --- M3/M7: price_history ---
    def upsert_price(self, *, symbol: str, bucket_date: str, close: float,
                     ret: float | None, collected_at: str) -> None:
        self.conn.execute(
            """
            INSERT INTO price_history (symbol, bucket_date, close, ret, collected_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol, bucket_date) DO UPDATE SET
                close = excluded.close, ret = excluded.ret,
                collected_at = excluded.collected_at
            """,
            (symbol, bucket_date, close, ret, collected_at),
        )
        self.conn.commit()

    def fetch_aggregates_asc(self, theme: str, scope: str, limit: int) -> list[sqlite3.Row]:
        """최근 N일을 날짜 오름차순으로 (규칙의 consecutive_days 평가용)."""
        rows = self.conn.execute(
            """
            SELECT * FROM daily_aggregates
            WHERE theme = ? AND scope = ?
            ORDER BY bucket_date DESC LIMIT ?
            """,
            (theme, scope, limit),
        ).fetchall()
        return list(reversed(rows))


def get_db(path: Path | str | None = None, *, init: bool = True) -> Database:
    """DB 핸들 획득 (필요 시 스키마 초기화)."""
    db = Database(path)
    if init:
        db.init_schema()
    return db
