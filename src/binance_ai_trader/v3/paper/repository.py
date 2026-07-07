"""V3 Paper Order Repository — PostgreSQL backend.

Table: v3_paper_orders (PostgreSQL)
Linked to v3_candidates via signal_id (HOT-20260704-000001 format).
All time durations are computed in real-time from created_at/filled_at/closed_at.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from binance_ai_trader.v3.storage.pg import get_conn


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
    """All operations go to PostgreSQL."""

    def __init__(self, db_path=None) -> None:
        pass

    def save(self, order: V3PaperOrder) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO v3_paper_orders
                       (order_id, signal_id, strategy_id, symbol, direction,
                        entry, stop_loss, tp1, tp2, rr, status, result,
                        created_at, filled_at, closed_at, expires_at,
                        pnl_pct, rr_realized, pushed, metadata_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (order_id) DO NOTHING""",
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
            conn.commit()
        finally:
            conn.close()

    def append_event(self, event: V3OrderEvent) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO v3_order_events
                       (event_id, order_id, signal_id, event_type, old_status, new_status,
                        candle_high, candle_low, triggered_at, metadata_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (event_id) DO NOTHING""",
                    (
                        event.event_id, event.order_id, event.signal_id,
                        event.event_type, event.old_status, event.new_status,
                        str(event.candle_high) if event.candle_high is not None else None,
                        str(event.candle_low) if event.candle_low is not None else None,
                        event.triggered_at, event.metadata_json,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def load_open(self) -> list[V3PaperOrder]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT order_id, signal_id, strategy_id, symbol, direction,
                              entry, stop_loss, tp1, tp2, rr, status, result,
                              created_at, filled_at, closed_at, expires_at,
                              pnl_pct, rr_realized, pushed, metadata_json
                       FROM v3_paper_orders
                       WHERE status IN ('OPEN','FILLED')
                       ORDER BY created_at"""
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_order(r) for r in rows]

    def load_open_by_strategy(self, strategy_id: str) -> list[V3PaperOrder]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT order_id, signal_id, strategy_id, symbol, direction,
                              entry, stop_loss, tp1, tp2, rr, status, result,
                              created_at, filled_at, closed_at, expires_at,
                              pnl_pct, rr_realized, pushed, metadata_json
                       FROM v3_paper_orders
                       WHERE strategy_id=%s AND status IN ('OPEN','FILLED')
                       ORDER BY created_at""",
                    (strategy_id,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_order(r) for r in rows]

    def load_all(self) -> list[V3PaperOrder]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT order_id, signal_id, strategy_id, symbol, direction,
                              entry, stop_loss, tp1, tp2, rr, status, result,
                              created_at, filled_at, closed_at, expires_at,
                              pnl_pct, rr_realized, pushed, metadata_json
                       FROM v3_paper_orders
                       ORDER BY created_at DESC"""
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_order(r) for r in rows]

    def load_recent_settled(self, n: int = 7) -> list[V3PaperOrder]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT order_id, signal_id, strategy_id, symbol, direction,
                              entry, stop_loss, tp1, tp2, rr, status, result,
                              created_at, filled_at, closed_at, expires_at,
                              pnl_pct, rr_realized, pushed, metadata_json
                       FROM v3_paper_orders
                       WHERE status='CLOSED' AND result IN ('TP1','TP2','SL','TIMEOUT')
                       ORDER BY closed_at DESC LIMIT %s""",
                    (n,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_order(r) for r in rows]

    def count_open_by_strategy(self, strategy_id: str) -> int:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM v3_paper_orders WHERE strategy_id=%s AND status IN ('OPEN','FILLED')",
                    (strategy_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return row[0] if row else 0

    def exists_open_for_symbol_direction(
        self, strategy_id: str, symbol: str, direction: str
    ) -> bool:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT 1 FROM v3_paper_orders
                       WHERE strategy_id=%s AND symbol=%s AND direction=%s
                         AND status IN ('OPEN','FILLED')
                       LIMIT 1""",
                    (strategy_id, symbol, direction),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return row is not None

    def update_filled(self, order_id: str, filled_at: str) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE v3_paper_orders SET status='FILLED', filled_at=%s WHERE order_id=%s",
                    (filled_at, order_id),
                )
            conn.commit()
        finally:
            conn.close()

    def update_settled(
        self,
        order_id: str,
        result: str,
        closed_at: str,
        pnl_pct: Decimal,
        rr_realized: Decimal,
    ) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE v3_paper_orders
                       SET status='CLOSED', result=%s, closed_at=%s,
                           pnl_pct=%s, rr_realized=%s
                       WHERE order_id=%s""",
                    (result, closed_at, str(pnl_pct), str(rr_realized), order_id),
                )
            conn.commit()
        finally:
            conn.close()

    def update_expired_not_filled(self, order_id: str, closed_at: str) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE v3_paper_orders
                       SET status='CLOSED', result='EXPIRED_NOT_FILLED', closed_at=%s
                       WHERE order_id=%s""",
                    (closed_at, order_id),
                )
            conn.commit()
        finally:
            conn.close()


def _row_to_order(row: tuple) -> V3PaperOrder:
    def _dec(v) -> Decimal | None:
        return Decimal(str(v)) if v is not None else None

    (order_id, signal_id, strategy_id, symbol, direction,
     entry, stop_loss, tp1, tp2, rr, status, result,
     created_at, filled_at, closed_at, expires_at,
     pnl_pct, rr_realized, pushed, metadata_json) = row

    return V3PaperOrder(
        order_id=order_id,
        signal_id=signal_id,
        strategy_id=strategy_id,
        symbol=symbol,
        direction=direction,
        entry=Decimal(str(entry)),
        stop_loss=Decimal(str(stop_loss)),
        tp1=Decimal(str(tp1)),
        tp2=Decimal(str(tp2)),
        rr=Decimal(str(rr)),
        status=status,
        result=result,
        created_at=created_at,
        filled_at=filled_at,
        closed_at=closed_at,
        expires_at=expires_at,
        pnl_pct=_dec(pnl_pct),
        rr_realized=_dec(rr_realized),
        pushed=bool(pushed),
        metadata_json=metadata_json or "{}",
    )
