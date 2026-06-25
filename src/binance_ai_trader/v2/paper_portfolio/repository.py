"""V2 Paper Portfolio — unified simulation order store."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class V2PaperOrder:
    order_id: str
    signal_id: str
    strategy_id: str
    symbol: str
    direction: str
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    rr: Decimal
    status: str
    result: str | None
    created_at: str
    filled_at: str | None
    closed_at: str | None
    expires_at: str
    pnl_pct: Decimal | None
    rr_realized: Decimal | None
    duration_minutes: int | None
    pushed: bool
    metadata_json: str


_DDL = """
CREATE TABLE IF NOT EXISTS v2_paper_orders (
    order_id         TEXT PRIMARY KEY,
    signal_id        TEXT NOT NULL,
    strategy_id      TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    direction        TEXT NOT NULL,
    entry            TEXT NOT NULL,
    stop_loss        TEXT NOT NULL,
    tp1              TEXT NOT NULL,
    tp2              TEXT NOT NULL,
    rr               TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'OPEN',
    result           TEXT,
    created_at       TEXT NOT NULL,
    filled_at        TEXT,
    closed_at        TEXT,
    expires_at       TEXT NOT NULL,
    pnl_pct          TEXT,
    rr_realized      TEXT,
    duration_minutes INTEGER,
    pushed           INTEGER NOT NULL DEFAULT 0,
    metadata_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS v2_paper_orders_status
    ON v2_paper_orders (status);
CREATE INDEX IF NOT EXISTS v2_paper_orders_strategy
    ON v2_paper_orders (strategy_id, status);
CREATE INDEX IF NOT EXISTS v2_paper_orders_symbol_dir
    ON v2_paper_orders (symbol, direction, status);
"""

_OPEN_STATUSES = ("OPEN", "FILLED")


class V2PaperOrderRepository:
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

    def save(self, order: V2PaperOrder) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_paper_orders
                    (order_id, signal_id, strategy_id, symbol, direction,
                     entry, stop_loss, tp1, tp2, rr, status, result,
                     created_at, filled_at, closed_at, expires_at,
                     pnl_pct, rr_realized, duration_minutes, pushed, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.order_id,
                    order.signal_id,
                    order.strategy_id,
                    order.symbol,
                    order.direction,
                    str(order.entry),
                    str(order.stop_loss),
                    str(order.tp1),
                    str(order.tp2),
                    str(order.rr),
                    order.status,
                    order.result,
                    order.created_at,
                    order.filled_at,
                    order.closed_at,
                    order.expires_at,
                    str(order.pnl_pct) if order.pnl_pct is not None else None,
                    str(order.rr_realized) if order.rr_realized is not None else None,
                    order.duration_minutes,
                    1 if order.pushed else 0,
                    order.metadata_json,
                ),
            )

    def load_open(self) -> list[V2PaperOrder]:
        placeholders = ",".join("?" for _ in _OPEN_STATUSES)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM v2_paper_orders WHERE status IN ({placeholders})",
                _OPEN_STATUSES,
            ).fetchall()
        return [_row_to_order(r) for r in rows]

    def load_all(self) -> list[V2PaperOrder]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_paper_orders ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_order(r) for r in rows]

    def count_open_by_strategy(self, strategy_id: str) -> int:
        placeholders = ",".join("?" for _ in _OPEN_STATUSES)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) FROM v2_paper_orders
                WHERE strategy_id = ? AND status IN ({placeholders})
                """,
                (strategy_id, *_OPEN_STATUSES),
            ).fetchone()
        return row[0] if row else 0

    def exists_open_for_symbol_direction(
        self, strategy_id: str, symbol: str, direction: str
    ) -> bool:
        placeholders = ",".join("?" for _ in _OPEN_STATUSES)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT 1 FROM v2_paper_orders
                WHERE strategy_id = ? AND symbol = ? AND direction = ?
                  AND status IN ({placeholders})
                LIMIT 1
                """,
                (strategy_id, symbol, direction, *_OPEN_STATUSES),
            ).fetchone()
        return row is not None

    def update_filled(self, order_id: str, filled_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE v2_paper_orders SET status = 'FILLED', filled_at = ? WHERE order_id = ?",
                (filled_at, order_id),
            )

    def update_settled(
        self,
        order_id: str,
        result: str,
        closed_at: str,
        pnl_pct: Decimal | None,
        rr_realized: Decimal | None,
        duration_minutes: int | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE v2_paper_orders
                SET status          = ?,
                    result          = ?,
                    closed_at       = ?,
                    pnl_pct         = ?,
                    rr_realized     = ?,
                    duration_minutes = ?
                WHERE order_id = ?
                """,
                (
                    result,
                    result,
                    closed_at,
                    str(pnl_pct) if pnl_pct is not None else None,
                    str(rr_realized) if rr_realized is not None else None,
                    duration_minutes,
                    order_id,
                ),
            )


def _row_to_order(row: sqlite3.Row) -> V2PaperOrder:
    return V2PaperOrder(
        order_id=row["order_id"],
        signal_id=row["signal_id"],
        strategy_id=row["strategy_id"],
        symbol=row["symbol"],
        direction=row["direction"],
        entry=Decimal(row["entry"]),
        stop_loss=Decimal(row["stop_loss"]),
        tp1=Decimal(row["tp1"]),
        tp2=Decimal(row["tp2"]),
        rr=Decimal(row["rr"]),
        status=row["status"],
        result=row["result"],
        created_at=row["created_at"],
        filled_at=row["filled_at"],
        closed_at=row["closed_at"],
        expires_at=row["expires_at"],
        pnl_pct=Decimal(row["pnl_pct"]) if row["pnl_pct"] else None,
        rr_realized=Decimal(row["rr_realized"]) if row["rr_realized"] else None,
        duration_minutes=row["duration_minutes"],
        pushed=bool(row["pushed"]),
        metadata_json=row["metadata_json"],
    )


def make_order_id() -> str:
    return f"v2ord-{uuid4()}"
