from __future__ import annotations

import sqlite3
from pathlib import Path

from binance_ai_trader.hotlist.models import HotlistAlert, HotlistWatchlistItem


class HotlistWatchlistRepository:
    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hotlist_watchlist (
                symbol TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                observation_count INTEGER NOT NULL,
                last_rank INTEGER NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hotlist_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry TEXT NOT NULL,
                created_at TEXT NOT NULL,
                stop_loss TEXT,
                tp1 TEXT,
                tp2 TEXT,
                rr TEXT,
                expires_at TEXT
            )
            """
        )
        for _col in ("stop_loss", "tp1", "tp2", "rr", "expires_at"):
            try:
                self._connection.execute(
                    f"ALTER TABLE hotlist_alerts ADD COLUMN {_col} TEXT"
                )
            except Exception:
                pass
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_hotlist_alerts_dedup
            ON hotlist_alerts(symbol, direction, created_at)
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def load(self, symbol: str) -> HotlistWatchlistItem | None:
        row = self._connection.execute(
            """
            SELECT symbol, source, first_seen_at, last_seen_at, expires_at,
                   observation_count, last_rank, status
            FROM hotlist_watchlist WHERE symbol=?
            """,
            (symbol,),
        ).fetchone()
        return _row(row) if row is not None else None

    def save(self, item: HotlistWatchlistItem) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO hotlist_watchlist (
                    symbol, source, first_seen_at, last_seen_at, expires_at,
                    observation_count, last_rank, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    source=excluded.source,
                    first_seen_at=excluded.first_seen_at,
                    last_seen_at=excluded.last_seen_at,
                    expires_at=excluded.expires_at,
                    observation_count=excluded.observation_count,
                    last_rank=excluded.last_rank,
                    status=excluded.status
                """,
                (
                    item.symbol,
                    item.source,
                    item.first_seen_at,
                    item.last_seen_at,
                    item.expires_at,
                    item.observation_count,
                    item.last_rank,
                    item.status,
                ),
            )

    def expire_before(self, timestamp: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE hotlist_watchlist SET status='EXPIRED'
                WHERE status='ACTIVE' AND expires_at <= ?
                """,
                (timestamp,),
            )

    def active(self) -> tuple[HotlistWatchlistItem, ...]:
        rows = self._connection.execute(
            """
            SELECT symbol, source, first_seen_at, last_seen_at, expires_at,
                   observation_count, last_rank, status
            FROM hotlist_watchlist
            WHERE status='ACTIVE'
            ORDER BY last_rank, symbol
            """
        ).fetchall()
        return tuple(_row(row) for row in rows)

    def all(self) -> tuple[HotlistWatchlistItem, ...]:
        rows = self._connection.execute(
            """
            SELECT symbol, source, first_seen_at, last_seen_at, expires_at,
                   observation_count, last_rank, status
            FROM hotlist_watchlist ORDER BY symbol
            """
        ).fetchall()
        return tuple(_row(row) for row in rows)

    def has_recent_alert(
        self, symbol: str, direction: str, entry: str, created_after: str
    ) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM hotlist_alerts
            WHERE symbol=? AND direction=? AND entry=? AND created_at>?
            LIMIT 1
            """,
            (symbol, direction, entry, created_after),
        ).fetchone()
        return row is not None

    def save_alert(self, alert: HotlistAlert) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO hotlist_alerts(
                    symbol, direction, entry, created_at,
                    stop_loss, tp1, tp2, rr, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.symbol,
                    alert.direction,
                    str(alert.entry),
                    alert.created_at,
                    str(alert.plan.stop_loss),
                    str(alert.plan.tp1),
                    str(alert.plan.tp2),
                    str(alert.plan.rr),
                    alert.plan.expires_at,
                ),
            )

    def has_recent_alert_cooldown(self, symbol: str, cutoff_iso: str) -> bool:
        """Rule 2/4: symbol-only cooldown — any direction or entry."""
        row = self._connection.execute(
            """
            SELECT 1 FROM hotlist_alerts
            WHERE symbol=? AND created_at>?
            LIMIT 1
            """,
            (symbol, cutoff_iso),
        ).fetchone()
        return row is not None

    def has_open_opportunity(self, symbol: str, now_iso: str) -> bool:
        """Rule 1: non-expired entry in hotlist_opportunities (table may not exist)."""
        try:
            row = self._connection.execute(
                """
                SELECT 1 FROM hotlist_opportunities
                WHERE symbol=? AND expiry>?
                LIMIT 1
                """,
                (symbol, now_iso),
            ).fetchone()
            return row is not None
        except sqlite3.OperationalError:
            return False

    def open_opportunity_direction(self, symbol: str, now_iso: str) -> str | None:
        """Rule 3: direction of most-recent non-expired opportunity, or None."""
        try:
            row = self._connection.execute(
                """
                SELECT direction FROM hotlist_opportunities
                WHERE symbol=? AND expiry>?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (symbol, now_iso),
            ).fetchone()
            return str(row[0]) if row is not None else None
        except sqlite3.OperationalError:
            return None

    def alert_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM hotlist_alerts"
        ).fetchone()
        return int(row[0])


def _row(row: tuple[object, ...]) -> HotlistWatchlistItem:
    return HotlistWatchlistItem(
        symbol=str(row[0]),
        source=str(row[1]),
        first_seen_at=str(row[2]),
        last_seen_at=str(row[3]),
        expires_at=str(row[4]),
        observation_count=int(row[5]),
        last_rank=int(row[6]),
        status=str(row[7]),
    )
