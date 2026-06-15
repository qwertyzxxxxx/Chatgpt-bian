from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.hotlist.models import (
    HotlistOutcome,
    TrackedHotlistOpportunity,
)


class HotlistPerformanceRepository:
    """Persist research opportunities and their public-market outcomes."""

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS hotlist_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry TEXT NOT NULL,
                sl TEXT NOT NULL,
                tp1 TEXT NOT NULL,
                tp2 TEXT NOT NULL,
                rr TEXT NOT NULL,
                confidence TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expiry TEXT NOT NULL,
                UNIQUE(symbol, direction, entry, created_at)
            );
            CREATE TABLE IF NOT EXISTS hotlist_outcomes (
                opportunity_id INTEGER NOT NULL,
                horizon_hours INTEGER NOT NULL,
                status TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                return_pct TEXT NOT NULL,
                PRIMARY KEY(opportunity_id, horizon_hours),
                FOREIGN KEY(opportunity_id) REFERENCES hotlist_opportunities(id)
            );
            """
        )

    def close(self) -> None:
        self._connection.close()

    def save_opportunity(
        self, opportunity: TrackedHotlistOpportunity
    ) -> TrackedHotlistOpportunity:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO hotlist_opportunities
                (symbol, direction, entry, sl, tp1, tp2, rr, confidence, created_at, expiry)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity.symbol,
                opportunity.direction,
                str(opportunity.entry),
                str(opportunity.stop_loss),
                str(opportunity.tp1),
                str(opportunity.tp2),
                str(opportunity.rr),
                opportunity.confidence,
                opportunity.created_at,
                opportunity.expires_at,
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            """
            SELECT * FROM hotlist_opportunities
            WHERE symbol = ? AND direction = ? AND entry = ? AND created_at = ?
            """,
            (
                opportunity.symbol,
                opportunity.direction,
                str(opportunity.entry),
                opportunity.created_at,
            ),
        ).fetchone()
        return self._opportunity(row)

    def opportunities(self, limit: int | None = None) -> tuple[TrackedHotlistOpportunity, ...]:
        sql = "SELECT * FROM hotlist_opportunities ORDER BY created_at DESC, id DESC"
        parameters: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (limit,)
        return tuple(
            self._opportunity(row)
            for row in self._connection.execute(sql, parameters).fetchall()
        )

    def save_outcome(self, outcome: HotlistOutcome) -> None:
        self._connection.execute(
            """
            INSERT INTO hotlist_outcomes
                (opportunity_id, horizon_hours, status, evaluated_at, return_pct)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(opportunity_id, horizon_hours) DO UPDATE SET
                status = excluded.status,
                evaluated_at = excluded.evaluated_at,
                return_pct = excluded.return_pct
            """,
            (
                outcome.opportunity_id,
                outcome.horizon_hours,
                outcome.status,
                outcome.evaluated_at,
                str(outcome.return_pct),
            ),
        )
        self._connection.commit()

    def outcomes(self) -> tuple[HotlistOutcome, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM hotlist_outcomes
            ORDER BY opportunity_id, horizon_hours
            """
        ).fetchall()
        return tuple(
            HotlistOutcome(
                opportunity_id=row["opportunity_id"],
                horizon_hours=row["horizon_hours"],
                status=row["status"],
                evaluated_at=row["evaluated_at"],
                return_pct=_decimal(row["return_pct"]),
            )
            for row in rows
        )

    @staticmethod
    def _opportunity(row: sqlite3.Row) -> TrackedHotlistOpportunity:
        return TrackedHotlistOpportunity(
            id=row["id"],
            symbol=row["symbol"],
            direction=row["direction"],
            entry=_decimal(row["entry"]),
            stop_loss=_decimal(row["sl"]),
            tp1=_decimal(row["tp1"]),
            tp2=_decimal(row["tp2"]),
            rr=_decimal(row["rr"]),
            confidence=row["confidence"],
            created_at=row["created_at"],
            expires_at=row["expiry"],
        )


def _decimal(value: str) -> Decimal:
    return Decimal(value)
