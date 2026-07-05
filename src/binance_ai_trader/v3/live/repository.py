"""Live Mirror PostgreSQL repository — live_orders and live_events."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from binance_ai_trader.v3.live.models import LiveEvent, LiveOrder, make_live_event_id
from binance_ai_trader.v3.storage.pg import get_conn

log = logging.getLogger(__name__)


class LiveOrderRepository:
    def save(self, order: LiveOrder) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO live_orders (
                        live_order_id, signal_id, symbol, side, direction,
                        entry, sl, tp, notional, leverage, quantity,
                        status, entry_order_id, sl_order_id, tp_order_id,
                        created_at, updated_at, reject_reason
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    ON CONFLICT (live_order_id) DO NOTHING
                    """,
                    (
                        order.live_order_id, order.signal_id, order.symbol,
                        order.side, order.direction, order.entry, order.sl,
                        order.tp, order.notional, order.leverage, order.quantity,
                        order.status, order.entry_order_id, order.sl_order_id,
                        order.tp_order_id, order.created_at, order.updated_at,
                        order.reject_reason,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def update_status(
        self,
        live_order_id: str,
        status: str,
        sl_order_id: str | None = None,
        tp_order_id: str | None = None,
        reject_reason: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE live_orders SET
                        status       = %s,
                        updated_at   = %s,
                        sl_order_id  = COALESCE(%s, sl_order_id),
                        tp_order_id  = COALESCE(%s, tp_order_id),
                        reject_reason = COALESCE(%s, reject_reason)
                    WHERE live_order_id = %s
                    """,
                    (status, now, sl_order_id, tp_order_id, reject_reason, live_order_id),
                )
            conn.commit()
        finally:
            conn.close()

    def update_entry_order_id(self, live_order_id: str, entry_order_id: str) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE live_orders SET entry_order_id=%s, updated_at=%s WHERE live_order_id=%s",
                    (entry_order_id, now, live_order_id),
                )
            conn.commit()
        finally:
            conn.close()

    def load_by_status(self, *statuses: str) -> list[LiveOrder]:
        placeholders = ",".join(["%s"] * len(statuses))
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM live_orders WHERE status IN ({placeholders}) ORDER BY created_at",
                    list(statuses),
                )
                return [_row_to_order(row, cur.description) for row in cur.fetchall()]
        finally:
            conn.close()

    def load_active_symbols(self) -> set[str]:
        """Symbols with PENDING or FILLED orders."""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT symbol FROM live_orders WHERE status IN ('PENDING','FILLED')")
                return {row[0] for row in cur.fetchall()}
        finally:
            conn.close()

    def load_recent(self, limit: int = 20) -> list[LiveOrder]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM live_orders ORDER BY created_at DESC LIMIT %s", (limit,)
                )
                return [_row_to_order(row, cur.description) for row in cur.fetchall()]
        finally:
            conn.close()

    def append_event(self, event: LiveEvent) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO live_events (event_id, live_order_id, signal_id, event_type, details_json, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (
                        event.event_id, event.live_order_id, event.signal_id,
                        event.event_type, event.details_json, event.created_at,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def count_today_by_type(self, event_type: str) -> int:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM live_events WHERE event_type=%s AND created_at LIKE %s",
                    (event_type, f"{today}%"),
                )
                return cur.fetchone()[0]
        finally:
            conn.close()

    def today_order_count(self) -> int:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM live_orders WHERE created_at LIKE %s", (f"{today}%",)
                )
                return cur.fetchone()[0]
        finally:
            conn.close()


def _row_to_order(row: tuple, description: object) -> LiveOrder:
    cols = [d[0] for d in description]
    d = dict(zip(cols, row))
    return LiveOrder(
        live_order_id  = d["live_order_id"],
        signal_id      = d["signal_id"],
        symbol         = d["symbol"],
        side           = d["side"],
        direction      = d["direction"],
        entry          = d["entry"],
        sl             = d["sl"],
        tp             = d["tp"],
        notional       = d["notional"],
        leverage       = int(d["leverage"]),
        quantity       = d["quantity"],
        status         = d["status"],
        entry_order_id = d["entry_order_id"],
        sl_order_id    = d["sl_order_id"],
        tp_order_id    = d["tp_order_id"],
        created_at     = d["created_at"],
        updated_at     = d["updated_at"],
        reject_reason  = d["reject_reason"],
    )
