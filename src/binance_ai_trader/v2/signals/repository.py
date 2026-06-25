"""V2 Signal store — strategies write here, Paper Portfolio reads here."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class V2Signal:
    signal_id: str
    strategy_id: str
    symbol: str
    direction: str
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    rr: Decimal
    reason: str
    metadata_json: str
    created_at: str


_DDL = """
CREATE TABLE IF NOT EXISTS v2_signals (
    signal_id     TEXT PRIMARY KEY,
    strategy_id   TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    direction     TEXT NOT NULL,
    entry         TEXT NOT NULL,
    stop_loss     TEXT NOT NULL,
    tp1           TEXT NOT NULL,
    tp2           TEXT NOT NULL,
    rr            TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS v2_signals_strategy_symbol_dir
    ON v2_signals (strategy_id, symbol, direction, created_at);
"""


class V2SignalRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_table(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)

    def save(self, signal: V2Signal) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_signals
                    (signal_id, strategy_id, symbol, direction, entry, stop_loss,
                     tp1, tp2, rr, reason, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.strategy_id,
                    signal.symbol,
                    signal.direction,
                    str(signal.entry),
                    str(signal.stop_loss),
                    str(signal.tp1),
                    str(signal.tp2),
                    str(signal.rr),
                    signal.reason,
                    signal.metadata_json,
                    signal.created_at,
                ),
            )

    def exists_recent(
        self,
        strategy_id: str,
        symbol: str,
        direction: str,
        hours: int = 24,
    ) -> bool:
        """Return True if a signal with the same strategy/symbol/direction
        was already saved within the last ``hours`` hours (dedup window)."""
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM v2_signals
                WHERE strategy_id = ? AND symbol = ? AND direction = ?
                  AND created_at >= ?
                LIMIT 1
                """,
                (strategy_id, symbol, direction, cutoff),
            ).fetchone()
        return row is not None

    def load_recent(self, strategy_id: str, hours: int = 24) -> list[V2Signal]:
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM v2_signals
                WHERE strategy_id = ? AND created_at >= ?
                ORDER BY created_at DESC
                """,
                (strategy_id, cutoff),
            ).fetchall()
        return [_row_to_signal(r) for r in rows]

    def load_all(self) -> list[V2Signal]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_signals ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_signal(r) for r in rows]


def _row_to_signal(row: sqlite3.Row) -> V2Signal:
    return V2Signal(
        signal_id=row["signal_id"],
        strategy_id=row["strategy_id"],
        symbol=row["symbol"],
        direction=row["direction"],
        entry=Decimal(row["entry"]),
        stop_loss=Decimal(row["stop_loss"]),
        tp1=Decimal(row["tp1"]),
        tp2=Decimal(row["tp2"]),
        rr=Decimal(row["rr"]),
        reason=row["reason"],
        metadata_json=row["metadata_json"],
        created_at=row["created_at"],
    )


def make_signal_id() -> str:
    return f"sig-{uuid4()}"
