"""Unified paper order repository — paper_orders table."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class PaperOrder:
    order_id: str
    strategy_id: str
    source_type: str
    source_id: str
    symbol: str
    direction: str
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    rr: Decimal
    status: str
    result: str | None
    pushed: bool
    alert_id: str | None
    created_at: str
    filled_at: str | None
    closed_at: str | None
    expires_at: str
    pnl_pct: Decimal | None
    rr_realized: Decimal | None
    duration_minutes: int | None
    legacy: bool


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS paper_orders (
    order_id        TEXT PRIMARY KEY,
    strategy_id     TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_id       TEXT NOT NULL UNIQUE,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry           TEXT NOT NULL,
    stop_loss       TEXT NOT NULL,
    tp1             TEXT NOT NULL,
    tp2             TEXT NOT NULL,
    rr              TEXT NOT NULL DEFAULT '0',
    status          TEXT NOT NULL DEFAULT 'OPEN',
    result          TEXT,
    pushed          INTEGER NOT NULL DEFAULT 0,
    alert_id        TEXT,
    created_at      TEXT NOT NULL,
    filled_at       TEXT,
    closed_at       TEXT,
    expires_at      TEXT NOT NULL,
    pnl_pct         TEXT,
    rr_realized     TEXT,
    duration_minutes INTEGER,
    legacy          INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_INDEX_SOURCE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_source_id
ON paper_orders(source_id)
"""

_CREATE_INDEX_SYMBOL = """
CREATE INDEX IF NOT EXISTS idx_paper_orders_symbol_status
ON paper_orders(symbol, status)
"""

_OPEN_STATUSES = ("OPEN", "FILLED")

_SETTLE_STATUSES = ("TP1", "TP2", "SL", "EXPIRED_NOT_FILLED", "TIMEOUT", "CANCELLED")


def _dec(val: str | None) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(val)
    except Exception:
        return None


def _int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except Exception:
        return None


def _row_to_order(row: sqlite3.Row) -> PaperOrder:
    return PaperOrder(
        order_id=row["order_id"],
        strategy_id=row["strategy_id"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        symbol=row["symbol"],
        direction=row["direction"],
        entry=Decimal(row["entry"]),
        stop_loss=Decimal(row["stop_loss"]),
        tp1=Decimal(row["tp1"]),
        tp2=Decimal(row["tp2"]),
        rr=Decimal(row["rr"]),
        status=row["status"],
        result=row["result"],
        pushed=bool(row["pushed"]),
        alert_id=row["alert_id"],
        created_at=row["created_at"],
        filled_at=row["filled_at"],
        closed_at=row["closed_at"],
        expires_at=row["expires_at"],
        pnl_pct=_dec(row["pnl_pct"]),
        rr_realized=_dec(row["rr_realized"]),
        duration_minutes=_int(row["duration_minutes"]),
        legacy=bool(row["legacy"]),
    )


class PaperOrderRepository:
    """Manages the paper_orders table for unified strategy simulation."""

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(database))
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute(_CREATE_TABLE)
        self._con.execute(_CREATE_INDEX_SOURCE)
        self._con.execute(_CREATE_INDEX_SYMBOL)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def source_id_exists(self, source_id: str) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM paper_orders WHERE source_id=? LIMIT 1", (source_id,)
        ).fetchone()
        return row is not None

    def save(self, order: PaperOrder) -> None:
        self._con.execute(
            """
            INSERT OR IGNORE INTO paper_orders (
                order_id, strategy_id, source_type, source_id,
                symbol, direction, entry, stop_loss, tp1, tp2, rr,
                status, result, pushed, alert_id,
                created_at, filled_at, closed_at, expires_at,
                pnl_pct, rr_realized, duration_minutes, legacy
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                order.order_id,
                order.strategy_id,
                order.source_type,
                order.source_id,
                order.symbol,
                order.direction,
                str(order.entry),
                str(order.stop_loss),
                str(order.tp1),
                str(order.tp2),
                str(order.rr),
                order.status,
                order.result,
                int(order.pushed),
                order.alert_id,
                order.created_at,
                order.filled_at,
                order.closed_at,
                order.expires_at,
                str(order.pnl_pct) if order.pnl_pct is not None else None,
                str(order.rr_realized) if order.rr_realized is not None else None,
                order.duration_minutes,
                int(order.legacy),
            ),
        )
        self._con.commit()

    def update_filled(self, order_id: str, filled_at: str) -> None:
        self._con.execute(
            "UPDATE paper_orders SET status='FILLED', filled_at=? WHERE order_id=?",
            (filled_at, order_id),
        )
        self._con.commit()

    def update_settled(
        self,
        order_id: str,
        result: str,
        closed_at: str,
        pnl_pct: Decimal | None,
        rr_realized: Decimal | None,
        duration_minutes: int | None,
    ) -> None:
        self._con.execute(
            """
            UPDATE paper_orders
            SET status=?, result=?, closed_at=?,
                pnl_pct=?, rr_realized=?, duration_minutes=?
            WHERE order_id=?
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
        self._con.commit()

    def load_open(self) -> list[PaperOrder]:
        rows = self._con.execute(
            "SELECT * FROM paper_orders WHERE status IN ('OPEN','FILLED')"
        ).fetchall()
        return [_row_to_order(r) for r in rows]

    def load_all(
        self,
        strategy_id: str | None = None,
        pushed: bool | None = None,
        status: str | None = None,
        result: str | None = None,
        symbol: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 200,
    ) -> list[PaperOrder]:
        clauses: list[str] = ["1=1"]
        params: list = []
        if strategy_id:
            clauses.append("strategy_id=?"); params.append(strategy_id)
        if pushed is not None:
            clauses.append("pushed=?"); params.append(int(pushed))
        if status:
            clauses.append("status=?"); params.append(status)
        if result:
            clauses.append("result=?"); params.append(result)
        if symbol:
            clauses.append("symbol=?"); params.append(symbol)
        if since:
            clauses.append("created_at>=?"); params.append(since)
        if until:
            clauses.append("created_at<=?"); params.append(until)
        params.append(limit)
        rows = self._con.execute(
            f"SELECT * FROM paper_orders WHERE {' AND '.join(clauses)}"
            f" ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_order(r) for r in rows]

    def load_pushed_since(self, since: str) -> list[PaperOrder]:
        rows = self._con.execute(
            "SELECT * FROM paper_orders WHERE pushed=1 AND created_at>=?"
            " ORDER BY created_at DESC",
            (since,),
        ).fetchall()
        return [_row_to_order(r) for r in rows]

    def load_recent_pushed(self, n: int = 7) -> list[PaperOrder]:
        rows = self._con.execute(
            "SELECT * FROM paper_orders WHERE pushed=1"
            " ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [_row_to_order(r) for r in rows]

    def count_by_status(self, pushed: bool | None = None) -> dict[str, int]:
        if pushed is not None:
            rows = self._con.execute(
                "SELECT status, COUNT(*) FROM paper_orders WHERE pushed=? GROUP BY status",
                (int(pushed),),
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT status, COUNT(*) FROM paper_orders GROUP BY status"
            ).fetchall()
        return {r[0]: r[1] for r in rows}
