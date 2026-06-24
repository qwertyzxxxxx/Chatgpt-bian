from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import PoolStatus, PoolSummary, WatchDecision, WatchItem

_DDL = """
CREATE TABLE IF NOT EXISTS leaderboard_watch_items (
    watch_id             TEXT PRIMARY KEY,
    symbol               TEXT NOT NULL UNIQUE,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    first_rank_type      TEXT NOT NULL,
    latest_rank_type     TEXT NOT NULL,
    best_rank_position   INTEGER NOT NULL,
    latest_rank_position INTEGER NOT NULL,
    first_change_24h     TEXT NOT NULL,
    latest_change_24h    TEXT NOT NULL,
    quote_volume         TEXT NOT NULL,
    appearances_24h      INTEGER NOT NULL DEFAULT 1,
    status               TEXT NOT NULL DEFAULT 'NEW'
);

CREATE TABLE IF NOT EXISTS leaderboard_watch_reviews (
    review_id    TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    decision     TEXT NOT NULL,
    best_symbol  TEXT NOT NULL,
    direction    TEXT NOT NULL,
    entry        TEXT NOT NULL,
    stop_loss    TEXT NOT NULL,
    tp1          TEXT NOT NULL,
    tp2          TEXT NOT NULL,
    rr           TEXT NOT NULL,
    rating       TEXT NOT NULL,
    risk_level   TEXT NOT NULL,
    data_quality TEXT NOT NULL,
    raw_response TEXT NOT NULL,
    reasons      TEXT NOT NULL DEFAULT '[]',
    field_stats  TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS leaderboard_watch_candidates (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id              TEXT NOT NULL,
    symbol                 TEXT NOT NULL,
    rank_type              TEXT NOT NULL,
    rank_position          INTEGER NOT NULL,
    change_24h             TEXT NOT NULL,
    quote_volume           TEXT NOT NULL,
    active_duration_minutes INTEGER NOT NULL,
    data_quality           TEXT NOT NULL
);
"""


class LeaderboardWatchRepository:
    def __init__(self, db_path: str) -> None:
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._con.execute(stmt)
        self._migrate()
        self._con.commit()

    def _migrate(self) -> None:
        """Add columns missing from databases created by older schema versions."""
        cols = {
            row["name"]
            for row in self._con.execute(
                "PRAGMA table_info(leaderboard_watch_reviews)"
            ).fetchall()
        }
        if "field_stats" not in cols:
            self._con.execute(
                "ALTER TABLE leaderboard_watch_reviews"
                " ADD COLUMN field_stats TEXT NOT NULL DEFAULT '{}'"
            )

    def close(self) -> None:
        self._con.close()

    def upsert_item(self, item: WatchItem) -> None:
        existing = self._con.execute(
            "SELECT watch_id, best_rank_position, appearances_24h, status FROM leaderboard_watch_items WHERE symbol=?",
            (item.symbol,),
        ).fetchone()

        if existing is None:
            self._con.execute(
                """
                INSERT INTO leaderboard_watch_items
                  (watch_id, symbol, first_seen_at, last_seen_at, first_rank_type,
                   latest_rank_type, best_rank_position, latest_rank_position,
                   first_change_24h, latest_change_24h, quote_volume,
                   appearances_24h, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item.watch_id, item.symbol, item.first_seen_at,
                    item.last_seen_at, item.first_rank_type, item.latest_rank_type,
                    item.best_rank_position, item.latest_rank_position,
                    item.first_change_24h, item.latest_change_24h,
                    item.quote_volume, item.appearances_24h, item.status,
                ),
            )
        else:
            best_pos = min(existing["best_rank_position"], item.latest_rank_position)
            new_count = existing["appearances_24h"] + 1
            cur_status = existing["status"]
            new_status = cur_status if cur_status in ("OPEN", "CLOSED") else "ACTIVE"
            self._con.execute(
                """
                UPDATE leaderboard_watch_items SET
                    last_seen_at=?, latest_rank_type=?,
                    best_rank_position=?, latest_rank_position=?,
                    latest_change_24h=?, quote_volume=?,
                    appearances_24h=?, status=?
                WHERE symbol=?
                """,
                (
                    item.last_seen_at, item.latest_rank_type,
                    best_pos, item.latest_rank_position,
                    item.latest_change_24h, item.quote_volume,
                    new_count, new_status, item.symbol,
                ),
            )
        self._con.commit()

    def expire_stale(self, watch_hours: int = 24) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=watch_hours)
        ).isoformat(timespec="seconds")
        cur = self._con.execute(
            """
            UPDATE leaderboard_watch_items
            SET status='EXPIRED'
            WHERE status IN ('NEW','ACTIVE') AND last_seen_at < ?
            """,
            (cutoff,),
        )
        self._con.commit()
        return cur.rowcount

    def active_items(self, limit: int = 20) -> list[WatchItem]:
        rows = self._con.execute(
            """
            SELECT * FROM leaderboard_watch_items
            WHERE status IN ('NEW','ACTIVE')
            ORDER BY best_rank_position ASC, latest_change_24h DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_to_item(r) for r in rows]

    def items_for_gemini(
        self,
        max_n: int = 20,
        closed_cooldown_hours: float = 4.0,
        min_move_pct: float = 8.0,
    ) -> list[WatchItem]:
        """Return NEW+ACTIVE candidates for Gemini, applying movement and cooldown filters.

        Exclusions:
        - OPEN items (already in an active trade recommendation)
        - EXPIRED items
        - CLOSED items within the cooldown window
        - Items where abs(latest_change_24h) < min_move_pct (catches low-volatility
          VOLUME coins such as BTC/ETH that have no actionable edge)

        Ordering: best_rank_position ASC, abs(change_24h) DESC, quote_volume DESC
        """
        closed_cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=closed_cooldown_hours)
        ).isoformat(timespec="seconds")
        rows = self._con.execute(
            """
            SELECT * FROM leaderboard_watch_items
            WHERE status IN ('NEW', 'ACTIVE')
              AND ABS(CAST(latest_change_24h AS REAL)) >= ?
              AND NOT (
                status = 'CLOSED' AND last_seen_at >= ?
              )
            ORDER BY
              best_rank_position ASC,
              ABS(CAST(latest_change_24h AS REAL)) DESC,
              CAST(quote_volume AS REAL) DESC
            LIMIT ?
            """,
            (min_move_pct, closed_cutoff, max_n),
        ).fetchall()
        return [_to_item(r) for r in rows]

    def pool_status(self) -> PoolStatus:
        counts: dict[str, int] = {}
        for row in self._con.execute(
            "SELECT status, COUNT(*) as cnt FROM leaderboard_watch_items GROUP BY status"
        ).fetchall():
            counts[row["status"]] = row["cnt"]

        top = self._con.execute(
            """
            SELECT * FROM leaderboard_watch_items
            WHERE status IN ('NEW','ACTIVE')
            ORDER BY best_rank_position ASC, appearances_24h DESC
            LIMIT 20
            """
        ).fetchall()

        return PoolStatus(
            new_count=counts.get("NEW", 0),
            active_count=counts.get("ACTIVE", 0),
            open_count=counts.get("OPEN", 0),
            closed_count=counts.get("CLOSED", 0),
            expired_count=counts.get("EXPIRED", 0),
            top_active=[_to_item(r) for r in top],
        )

    def save_review(
        self,
        review_id: str,
        decision: WatchDecision,
        field_stats: str = "{}",
    ) -> None:
        import json
        self._con.execute(
            """
            INSERT OR REPLACE INTO leaderboard_watch_reviews
              (review_id, created_at, decision, best_symbol, direction,
               entry, stop_loss, tp1, tp2, rr, rating, risk_level,
               data_quality, raw_response, reasons, field_stats, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                review_id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                decision.decision,
                decision.best_symbol,
                decision.direction,
                decision.entry,
                decision.stop_loss,
                decision.tp1,
                decision.tp2,
                decision.rr,
                decision.rating,
                decision.risk_level,
                decision.data_quality,
                decision.raw_response[:4000],
                json.dumps(decision.reasons),
                field_stats,
                "OPEN",
            ),
        )
        if decision.should_trade and decision.best_symbol not in ("NONE", "UNKNOWN"):
            self._con.execute(
                "UPDATE leaderboard_watch_items SET status='OPEN' WHERE symbol=?",
                (decision.best_symbol,),
            )
        self._con.commit()

    def save_candidates(self, review_id: str, candidates: list[Any]) -> None:
        rows = [
            (
                review_id, c.symbol, c.latest_rank_type,
                c.latest_rank_position, c.latest_change_24h,
                c.quote_volume, c.active_duration_minutes, c.data_quality,
            )
            for c in candidates
        ]
        self._con.executemany(
            """
            INSERT INTO leaderboard_watch_candidates
              (review_id, symbol, rank_type, rank_position, change_24h,
               quote_volume, active_duration_minutes, data_quality)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        self._con.commit()

    def recent_reviews(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM leaderboard_watch_reviews ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def candidates_for_reviews(self, review_ids: list[str]) -> list[dict[str, Any]]:
        if not review_ids:
            return []
        placeholders = ",".join("?" * len(review_ids))
        rows = self._con.execute(
            f"SELECT * FROM leaderboard_watch_candidates WHERE review_id IN ({placeholders}) ORDER BY review_id, rank_position",
            review_ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def last_review_at(self) -> datetime | None:
        row = self._con.execute(
            "SELECT created_at FROM leaderboard_watch_reviews ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["created_at"])

    def has_open_review(self) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM leaderboard_watch_reviews WHERE decision='TRADE' AND status='OPEN' LIMIT 1"
        ).fetchone()
        return row is not None

    def settle_review(self, review_id: str, outcome: str) -> None:
        self._con.execute(
            "UPDATE leaderboard_watch_reviews SET status=? WHERE review_id=?",
            (outcome, review_id),
        )
        row = self._con.execute(
            "SELECT best_symbol FROM leaderboard_watch_reviews WHERE review_id=?",
            (review_id,),
        ).fetchone()
        if row:
            self._con.execute(
                "UPDATE leaderboard_watch_items SET status='CLOSED' WHERE symbol=?",
                (row["best_symbol"],),
            )
        self._con.commit()

    def open_reviews(self) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM leaderboard_watch_reviews WHERE decision='TRADE' AND status='OPEN' ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def pool_summary(self) -> PoolSummary:
        rows = self._con.execute(
            "SELECT status, COUNT(*) as cnt FROM leaderboard_watch_reviews WHERE decision='TRADE' GROUP BY status"
        ).fetchall()
        counts: dict[str, int] = {r["status"]: r["cnt"] for r in rows}
        no_trade = self._con.execute(
            "SELECT COUNT(*) as cnt FROM leaderboard_watch_reviews WHERE decision='NO_TRADE'"
        ).fetchone()["cnt"]
        total = self._con.execute(
            "SELECT COUNT(*) as cnt FROM leaderboard_watch_reviews"
        ).fetchone()["cnt"]

        tp1 = counts.get("TP1", 0)
        tp2 = counts.get("TP2", 0)
        sl = counts.get("SL", 0)
        timeout = counts.get("TIMEOUT", 0)
        trade_settled = tp1 + tp2 + sl + timeout
        win_rate = f"{(tp1 + tp2) / trade_settled * 100:.1f}%" if trade_settled > 0 else "N/A"

        return PoolSummary(
            total_reviews=total,
            trade_count=total - no_trade,
            no_trade_count=no_trade,
            open_count=counts.get("OPEN", 0),
            tp1_count=tp1,
            tp2_count=tp2,
            sl_count=sl,
            timeout_count=timeout,
            win_rate=win_rate,
        )


def _to_item(row: sqlite3.Row) -> WatchItem:
    return WatchItem(
        watch_id=row["watch_id"],
        symbol=row["symbol"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        first_rank_type=row["first_rank_type"],
        latest_rank_type=row["latest_rank_type"],
        best_rank_position=int(row["best_rank_position"]),
        latest_rank_position=int(row["latest_rank_position"]),
        first_change_24h=row["first_change_24h"],
        latest_change_24h=row["latest_change_24h"],
        quote_volume=row["quote_volume"],
        appearances_24h=int(row["appearances_24h"]),
        status=row["status"],
    )
