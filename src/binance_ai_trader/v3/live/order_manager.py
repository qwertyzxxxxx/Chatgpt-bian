"""Live order manager — same-symbol conflict resolution.

Pure decision logic (no I/O): given a new signal and the existing live state
for that symbol (PENDING orders + FILLED/open positions), decide what should
happen. The caller (LiveMirrorEngine) executes the Binance cancel + DB writes.

Rules
-----
1. Existing OPEN POSITION (status=FILLED) for the symbol:
     - same direction  -> POSITION_SAME_SIDE   (block, no pyramiding)
     - opposite         -> POSITION_OPPOSITE_SIDE (block, alert-only reminder,
                            never auto-flip/close)

2. Existing PENDING order for the symbol (checked only if no open position):
     - opposite direction -> CANCEL_CONFLICT (cancel old pending order, new
       signal is NOT placed either — no auto-flip)
     - same direction, entry meaningfully better (>=0.5% closer to a
       favourable fill) -> REPLACE (cancel old, place new)
     - same direction, entry within +/-0.5% -> IGNORE_DUPLICATE (keep old)
     - same direction, entry meaningfully worse -> IGNORE_WORSE_ENTRY

3. No conflict -> PLACE
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from binance_ai_trader.v3.live.models import LiveOrder

_ENTRY_TOLERANCE = Decimal("0.005")  # 0.5%


@dataclass(frozen=True, slots=True)
class ConflictDecision:
    action: str  # PLACE | REPLACE | IGNORE_DUPLICATE | IGNORE_WORSE_ENTRY |
                 # CANCEL_CONFLICT | POSITION_SAME_SIDE | POSITION_OPPOSITE_SIDE
    reason: str
    conflicting_order: LiveOrder | None = None


class LiveOrderManager:
    def resolve(
        self,
        symbol: str,
        direction: str,
        new_entry: Decimal,
        pending_orders: list[LiveOrder],
        filled_orders: list[LiveOrder],
    ) -> ConflictDecision:
        if filled_orders:
            existing = filled_orders[0]
            if existing.direction == direction:
                return ConflictDecision(
                    "POSITION_SAME_SIDE",
                    f"{symbol}已有同向持仓({existing.direction})，禁止加仓，跳过新信号",
                    existing,
                )
            return ConflictDecision(
                "POSITION_OPPOSITE_SIDE",
                f"{symbol}已有反向持仓({existing.direction})，不自动反手/平仓，仅提醒关注",
                existing,
            )

        if not pending_orders:
            return ConflictDecision("PLACE", "无同币种冲突")

        old = pending_orders[0]

        if old.direction != direction:
            return ConflictDecision(
                "CANCEL_CONFLICT",
                f"{symbol}方向冲突：旧挂单{old.direction}@{old.entry} vs 新信号{direction}@{new_entry}，"
                f"撤销旧挂单，新信号本次不下单",
                old,
            )

        old_entry = Decimal(old.entry) if old.entry else Decimal("0")
        if old_entry <= 0:
            return ConflictDecision("PLACE", "旧挂单入场价无效，按新信号处理")

        if direction == "LONG":
            better = new_entry <= old_entry * (1 - _ENTRY_TOLERANCE)
            worse = new_entry > old_entry * (1 + _ENTRY_TOLERANCE)
        else:
            better = new_entry >= old_entry * (1 + _ENTRY_TOLERANCE)
            worse = new_entry < old_entry * (1 - _ENTRY_TOLERANCE)

        if better:
            return ConflictDecision(
                "REPLACE",
                f"新信号入场价{new_entry}优于旧挂单{old_entry}(>0.5%)，替换旧挂单",
                old,
            )
        if worse:
            return ConflictDecision(
                "IGNORE_WORSE_ENTRY",
                f"新信号入场价{new_entry}劣于旧挂单{old_entry}(>0.5%)，忽略新信号，保留旧挂单",
                old,
            )
        return ConflictDecision(
            "IGNORE_DUPLICATE",
            f"新信号入场价{new_entry}与旧挂单{old_entry}接近(≤0.5%)，视为重复信号，保留旧挂单",
            old,
        )
