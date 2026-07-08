"""Tests for LiveOrderManager conflict-resolution rules (Part B spec)."""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.v3.live.order_manager import LiveOrderManager


def _order(
    direction: str,
    entry: str,
    status: str = "PENDING",
    live_order_id: str = "LIV-OLD",
    symbol: str = "BTCUSDT",
):
    from binance_ai_trader.v3.live.models import LiveOrder

    return LiveOrder(
        live_order_id=live_order_id,
        signal_id="SIG-OLD",
        symbol=symbol,
        side="BUY" if direction == "LONG" else "SELL",
        direction=direction,
        entry=entry,
        sl="0",
        tp="0",
        notional="1000",
        leverage=10,
        quantity="1",
        status=status,
        entry_order_id="BN-1",
        sl_order_id=None,
        tp_order_id=None,
        created_at="2026-07-08T00:00:00+00:00",
        updated_at="2026-07-08T00:00:00+00:00",
        reject_reason=None,
    )


def _mgr() -> LiveOrderManager:
    return LiveOrderManager()


def test_no_conflict_places():
    decision = _mgr().resolve("BTCUSDT", "LONG", Decimal("100"), [], [])
    assert decision.action == "PLACE"


def test_long_new_entry_meaningfully_better_replaces():
    old = _order("LONG", "100")
    decision = _mgr().resolve("BTCUSDT", "LONG", Decimal("99"), [old], [])
    assert decision.action == "REPLACE"
    assert decision.conflicting_order is old


def test_short_new_entry_meaningfully_better_replaces():
    old = _order("SHORT", "100")
    decision = _mgr().resolve("BTCUSDT", "SHORT", Decimal("101"), [old], [])
    assert decision.action == "REPLACE"


def test_long_entry_within_tolerance_is_duplicate():
    old = _order("LONG", "100")
    decision = _mgr().resolve("BTCUSDT", "LONG", Decimal("100.3"), [old], [])
    assert decision.action == "IGNORE_DUPLICATE"


def test_long_entry_meaningfully_worse_is_ignored():
    old = _order("LONG", "100")
    decision = _mgr().resolve("BTCUSDT", "LONG", Decimal("102"), [old], [])
    assert decision.action == "IGNORE_WORSE_ENTRY"


def test_short_entry_meaningfully_worse_is_ignored():
    old = _order("SHORT", "100")
    decision = _mgr().resolve("BTCUSDT", "SHORT", Decimal("98"), [old], [])
    assert decision.action == "IGNORE_WORSE_ENTRY"


def test_opposite_direction_pending_cancels_no_place():
    old = _order("LONG", "100")
    decision = _mgr().resolve("BTCUSDT", "SHORT", Decimal("100"), [old], [])
    assert decision.action == "CANCEL_CONFLICT"
    assert decision.conflicting_order is old


def test_existing_position_same_side_blocks():
    filled = _order("LONG", "100", status="FILLED")
    decision = _mgr().resolve("BTCUSDT", "LONG", Decimal("101"), [], [filled])
    assert decision.action == "POSITION_SAME_SIDE"


def test_existing_position_opposite_side_alert_only():
    filled = _order("LONG", "100", status="FILLED")
    decision = _mgr().resolve("BTCUSDT", "SHORT", Decimal("99"), [], [filled])
    assert decision.action == "POSITION_OPPOSITE_SIDE"


def test_boundary_exactly_at_half_percent_is_still_duplicate():
    # 0.5% tolerance is inclusive: exactly on the boundary counts as "within range".
    old = _order("LONG", "100")
    decision = _mgr().resolve("BTCUSDT", "LONG", Decimal("100.5"), [old], [])
    assert decision.action == "IGNORE_DUPLICATE"


def test_filled_position_takes_priority_over_pending_order():
    pending = _order("LONG", "100", live_order_id="LIV-PEND")
    filled = _order("LONG", "95", status="FILLED", live_order_id="LIV-FILLED")
    decision = _mgr().resolve("BTCUSDT", "LONG", Decimal("90"), [pending], [filled])
    assert decision.action == "POSITION_SAME_SIDE"
    assert decision.conflicting_order is filled
