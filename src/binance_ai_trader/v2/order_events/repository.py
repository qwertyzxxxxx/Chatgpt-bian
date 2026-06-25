"""V2 Order Events — append-only audit log for all order state transitions."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class V2OrderEvent:
    event_id: str
    order_id: str
    event_type: str
    old_status: str | None
    new_status: str
    candle_high: Decimal | None
    candle_low: Decimal | None
    triggered_at: str
    metadata_json: str


_DDL = """
CREATE TABLE IF NOT EXISTS v2_order_events (
    event_id      TEXT PRIMARY KEY,
    order_id      TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    old_status    TEXT,
    new_status    TEXT NOT NULL,
    candle_high   TEXT,
    candle_low    TEXT,
    triggered_at  TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS v2_order_events_order_id
    ON v2_order_events (order_id);
"""


class V2OrderEventRepository:
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

    def append(self, event: V2OrderEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_order_events
                    (event_id, order_id, event_type, old_status, new_status,
                     candle_high, candle_low, triggered_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.order_id,
                    event.event_type,
                    event.old_status,
                    event.new_status,
                    str(event.candle_high) if event.candle_high is not None else None,
                    str(event.candle_low) if event.candle_low is not None else None,
                    event.triggered_at,
                    event.metadata_json,
                ),
            )

    def load_for_order(self, order_id: str) -> list[V2OrderEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_order_events WHERE order_id = ? ORDER BY triggered_at",
                (order_id,),
            ).fetchall()
        return [_row_to_event(r) for r in rows]

    def count_for_order(self, order_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM v2_order_events WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        return row[0] if row else 0


def _row_to_event(row: sqlite3.Row) -> V2OrderEvent:
    return V2OrderEvent(
        event_id=row["event_id"],
        order_id=row["order_id"],
        event_type=row["event_type"],
        old_status=row["old_status"],
        new_status=row["new_status"],
        candle_high=Decimal(row["candle_high"]) if row["candle_high"] else None,
        candle_low=Decimal(row["candle_low"]) if row["candle_low"] else None,
        triggered_at=row["triggered_at"],
        metadata_json=row["metadata_json"],
    )


def make_event_id() -> str:
    return f"evt-{uuid4()}"
