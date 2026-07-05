"""Live Mirror data models."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


def make_live_order_id() -> str:
    return f"LIV-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"


def make_live_event_id() -> str:
    return f"LEV-{uuid4().hex[:12].upper()}"


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


@dataclass(frozen=True, slots=True)
class PlaceResult:
    ok: bool
    reason: str | None = None
    live_order_id: str | None = None

    def prefix(self) -> str:
        if self.ok:
            return "【已实盘】"
        return f"【实盘未下单：{self.reason}】"
