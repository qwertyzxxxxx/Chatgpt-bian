"""V3 Paper Order Repository — unified simulation order store.

Table: v3_paper_orders
Linked to v3_candidates via signal_id (HOT-20260704-000001 format).
All time durations are computed in real-time from created_at/filled_at/closed_at.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

_DDL = """
CREATE TABLE IF NOT EXISTS v3_paper_orders (
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
    pushed           INTEGER NOT NULL DEFAULT 1,
    metadata_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_v3_orders_strategy
    ON v3_paper_orders(strategy_id, status);
CREATE INDEX IF NOT EXISTS idx_v3_orders_symbol
    ON v3_paper_orders(symbol, direction, status);
CREATE INDEX IF NOT EXISTS idx_v3_orders_signal
    ON v3_paper_orders(signal_id);
CREATE INDEX IF NOT EXISTS idx_v3_orders_closed
    ON v3_paper_orders(closed_at);
"""

_ORDER_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS v3_order_events (
    event_id      TEXT PRIMARY KEY,
    order_id      TEXT NOT NULL,
    signal_id     TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    old_status    TEXT,
    new_status    TEXT,
    candle_high   TEXT,
    candle_low    TEXT,
    triggered_at  TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_v3_events_order
    ON v3_order_events(order_id);
"""


def make_order_id() -> str:
    return str(uuid4())


def make_event_id() -> str:
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class V3PaperOrder:
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
    pushed: bool
    metadata_json: str


@dataclass(frozen=True, slots=True)
class V3OrderEvent:
    event_id: str
    order_id: str
    signal_id: str
    event_type: str
    old_status: str | None
    new_status: str | None
    candle_high: Decimal | None
    candle_low: Decimal | None
    triggered_at: str
    metadata_json: str


class V3PaperOrderRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.executescript(_DDL)
            conn.executescript(_ORDER_EVENTS_DDL)

    def save(self, order: V3PaperOrder) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO v3_paper_orders
                   (order_id, signal_id, strategy_id, symbol, direction,
                    entry, stop_loss, tp1, tp2, rr, status, result,
                    created_at, filled_at, closed_at, expires_at,
                    pnl_pct, rr_realized, pushed, metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order.order_id, order.signal_id, order.strategy_id,
                    order.symbol, order.direction,
                    str(order.entry), str(order.stop_loss),
                    str(order.tp1), str(order.tp2), str(order.rr),
                    order.status, order.result,
                    order.created_at, order.filled_at, order.closed_at,
                    order.expires_at,
                    str(order.pnl_pct) if order.pnl_pct is not None else None,
                    str(order.rr_realized) if order.rr_realized is not None else None,
                    1 if order.pushed else 0,
                    order.metadata_json,
                ),
            )

    def append_event(self, event: V3OrderEvent) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """INSERT INTO v3_order_events
                   (event_id, order_id, signal_id, event_type, old_status, new_status,
                    candle_high, candle_low, triggered_at, metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.event_id, event.order_id, event.signal_id,
                    event.event_type, event.old_status, event.new_status,
                    str(event.candle_high) if event.candle_high is not None else None,
                    str(event.candle_low) if event.candle_low is not None else None,
                    event.triggered_at, event.metadata_json,
                ),
            )

    def load_open(self) -> list[V3PaperOrder]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM v3_paper_orders WHERE status IN ('OPEN','FILLED') ORDER BY created_at"
            ).fetchall()
        return [_row_to_order(r) for r in rows]

    def load_all(self) -> list[V3PaperOrder]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM v3_paper_orders ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_order(r) for r in rows]

    def load_recent_settled(self, n: int = 7) -> list[V3PaperOrder]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM v3_paper_orders
                   WHERE status='CLOSED' AND result IN ('TP1','TP2','SL','TIMEOUT')
                   ORDER BY closed_at DESC LIMIT ?""",
                (n,),
            ).fetchall()
        return [_row_to_order(r) for r in rows]

    def count_open_by_strategy(self, strategy_id: str) -> int:
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM v3_paper_orders WHERE strategy_id=? AND status IN ('OPEN','FILLED')",
                (strategy_id,),
            ).fetchone()
        return row[0] if row else 0

    def exists_open_for_symbol_direction(
        self, strategy_id: str, symbol: str, direction: str
    ) -> bool:
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                """SELECT 1 FROM v3_paper_orders
                   WHERE strategy_id=? AND symbol=? AND direction=?
                     AND status IN ('OPEN','FILLED')
                   LIMIT 1""",
                (strategy_id, symbol, direction),
            ).fetchone()
        return row is not None

    def update_filled(self, order_id: str, filled_at: str) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "UPDATE v3_paper_orders SET status='FILLED', filled_at=? WHERE order_id=?",
                (filled_at, order_id),
            )

    def update_settled(
        self,
        order_id: str,
        result: str,
        closed_at: str,
        pnl_pct: Decimal,
        rr_realized: Decimal,
    ) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """UPDATE v3_paper_orders
                   SET status='CLOSED', result=?, closed_at=?,
                       pnl_pct=?, rr_realized=?
                   WHERE order_id=?""",
                (result, closed_at, str(pnl_pct), str(rr_realized), order_id),
            )

    def update_expired_not_filled(self, order_id: str, closed_at: str) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """UPDATE v3_paper_orders
                   SET status='CLOSED', result='EXPIRED_NOT_FILLED', closed_at=?
                   WHERE order_id=?""",
                (closed_at, order_id),
            )


def _row_to_order(row: sqlite3.Row) -> V3PaperOrder:
    def _dec(v: str | None) -> Decimal | None:
        return Decimal(v) if v is not None else None

    return V3PaperOrder(
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
        pnl_pct=_dec(row["pnl_pct"]),
        rr_realized=_dec(row["rr_realized"]),
        pushed=bool(row["pushed"]),
        metadata_json=row["metadata_json"],
    )
