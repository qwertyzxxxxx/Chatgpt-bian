"""Live Mirror data models."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


def make_live_order_id() -> str:
    return f"LIV-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"


def make_live_event_id() -> str:
    return f"LEV-{uuid4().hex[:12].upper()}"


class LiveOrderStatus:
    """All valid `live_orders.status` values (plain strings, no DB enum)."""
    PENDING                     = "PENDING"
    FILLED                      = "FILLED"
    CLOSED_TP                   = "CLOSED_TP"
    CLOSED_SL                   = "CLOSED_SL"
    CANCELED                    = "CANCELED"
    REJECTED                    = "REJECTED"
    MANUAL_CLOSED               = "MANUAL_CLOSED"
    # Order-manager / conflict-resolution outcomes:
    REPLACED                    = "REPLACED"
    CANCELED_EXPIRED            = "CANCELED_EXPIRED"
    CANCELED_CONFLICT           = "CANCELED_CONFLICT"
    IGNORED_DUPLICATE           = "IGNORED_DUPLICATE"
    IGNORED_WORSE_ENTRY         = "IGNORED_WORSE_ENTRY"
    DIRECTION_CONFLICT          = "DIRECTION_CONFLICT"
    POSITION_EXISTS_SAME_SIDE   = "POSITION_EXISTS_SAME_SIDE"
    POSITION_EXISTS_OPPOSITE_SIDE = "POSITION_EXISTS_OPPOSITE_SIDE"


@dataclass(frozen=True, slots=True)
class LiveOrder:
    live_order_id: str
    signal_id: str
    symbol: str
    side: str             # BUY / SELL
    direction: str        # LONG / SHORT
    entry: str
    sl: str
    tp: str
    notional: str         # USDT notional
    leverage: int
    quantity: str
    status: str           # PENDING/FILLED/CLOSED_TP/CLOSED_SL/CANCELED/REJECTED/MANUAL_CLOSED
    entry_order_id: str | None
    sl_order_id: str | None
    tp_order_id: str | None
    created_at: str
    updated_at: str
    reject_reason: str | None


@dataclass(frozen=True, slots=True)
class LiveEvent:
    event_id: str
    live_order_id: str
    signal_id: str
    event_type: str
    details_json: str
    created_at: str
    # Conflict-management context (all optional — only populated by the order
    # manager when a new signal collides with an existing order/position).
    old_signal_id: str | None = None
    new_signal_id: str | None = None
    symbol: str | None = None
    old_side: str | None = None
    new_side: str | None = None
    old_entry: str | None = None
    new_entry: str | None = None
    action: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PlaceResult:
    ok: bool
    reason: str | None = None
    live_order_id: str | None = None

    def prefix(self) -> str:
        if self.ok:
            return "【已实盘】"
        return f"【实盘未下单：{self.reason}】"
