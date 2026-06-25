"""V2 Performance Engine — single source of truth, reads only v2_paper_orders.

Win-rate denominator: TP1 + TP2 + SL only.
OPEN, FILLED, EXPIRED_NOT_FILLED, TIMEOUT are excluded from the denominator.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from binance_ai_trader.v2.paper_portfolio.repository import V2PaperOrderRepository

_WIN_RESULTS = frozenset({"TP1", "TP2"})
_DENOMINATOR_RESULTS = frozenset({"TP1", "TP2", "SL"})


@dataclass(frozen=True, slots=True)
class V2Performance:
    strategy_id: str | None
    orders: int
    filled: int
    not_filled: int
    open_count: int
    tp1: int
    tp2: int
    sl: int
    expired_not_filled: int
    win_rate: Decimal
    avg_rr: Decimal
    avg_pnl: Decimal


class V2PerformanceCalculator:
    """Calculates performance stats from v2_paper_orders."""

    def __init__(self, repo: V2PaperOrderRepository) -> None:
        self._repo = repo

    def calculate(self, strategy_id: str | None = None) -> V2Performance:
        all_orders = self._repo.load_all()
        if strategy_id:
            all_orders = [o for o in all_orders if o.strategy_id == strategy_id]

        total = len(all_orders)
        filled = sum(1 for o in all_orders if o.status not in ("OPEN",) and o.result != "EXPIRED_NOT_FILLED")
        not_filled = sum(1 for o in all_orders if o.result == "EXPIRED_NOT_FILLED")
        open_count = sum(1 for o in all_orders if o.status in ("OPEN", "FILLED"))
        tp1 = sum(1 for o in all_orders if o.result == "TP1")
        tp2 = sum(1 for o in all_orders if o.result == "TP2")
        sl = sum(1 for o in all_orders if o.result == "SL")
        expired = sum(1 for o in all_orders if o.result == "EXPIRED_NOT_FILLED")

        denom_orders = [o for o in all_orders if o.result in _DENOMINATOR_RESULTS]
        win_orders = [o for o in denom_orders if o.result in _WIN_RESULTS]

        denom = len(denom_orders)
        win_rate = (
            Decimal(len(win_orders)) / Decimal(denom) * Decimal("100")
            if denom > 0 else Decimal("0")
        )

        rr_vals = [o.rr_realized for o in denom_orders if o.rr_realized is not None]
        avg_rr = sum(rr_vals, Decimal("0")) / Decimal(len(rr_vals)) if rr_vals else Decimal("0")

        pnl_vals = [o.pnl_pct for o in denom_orders if o.pnl_pct is not None]
        avg_pnl = sum(pnl_vals, Decimal("0")) / Decimal(len(pnl_vals)) if pnl_vals else Decimal("0")

        return V2Performance(
            strategy_id=strategy_id,
            orders=total,
            filled=filled,
            not_filled=not_filled,
            open_count=open_count,
            tp1=tp1,
            tp2=tp2,
            sl=sl,
            expired_not_filled=expired,
            win_rate=win_rate.quantize(Decimal("0.01")),
            avg_rr=avg_rr.quantize(Decimal("0.01")),
            avg_pnl=avg_pnl.quantize(Decimal("0.01")),
        )
