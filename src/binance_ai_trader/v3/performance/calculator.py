"""V3 Performance Calculator — unified, single-source-of-truth statistics.

Win Rate = TP1 / (TP1 + SL)   ← only these two count
TIMEOUT and EXPIRED_NOT_FILLED are excluded from win-rate denominator.

Time windows: All Time / 30d / 7d / 24h / Today
All windows computed from v3_paper_orders only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from binance_ai_trader.v3.paper.repository import V3PaperOrder, V3PaperOrderRepository


@dataclass(frozen=True, slots=True)
class V3Stats:
    """Unified stats for any time window."""
    window: str          # "all_time" | "30d" | "7d" | "24h" | "today"
    strategy_id: str
    signals: int         # from v3_candidates (passed in; orders only below)
    pushed: int
    filled: int
    settled: int
    tp1: int
    tp2: int
    sl: int
    timeout: int
    expired_not_filled: int
    open_count: int
    win_rate: Decimal    # TP1 / (TP1 + SL) × 100, or 0 if no data
    avg_rr: Decimal
    avg_pnl: Decimal     # average PnL% of TP1+SL orders


class V3PerformanceCalculator:
    def __init__(self, order_repo: V3PaperOrderRepository) -> None:
        self._order_repo = order_repo

    def calculate(self, strategy_id: str, window: str = "all_time") -> V3Stats:
        all_orders = self._order_repo.load_all()
        orders = [o for o in all_orders if o.strategy_id == strategy_id]
        orders = _filter_by_window(orders, window)
        return _compute_stats(strategy_id, window, orders)

    def calculate_all_windows(self, strategy_id: str) -> dict[str, V3Stats]:
        return {
            w: self.calculate(strategy_id, w)
            for w in ("all_time", "30d", "7d", "24h", "today")
        }


def _filter_by_window(orders: list[V3PaperOrder], window: str) -> list[V3PaperOrder]:
    if window == "all_time":
        return orders
    now = datetime.now(UTC)
    if window == "today":
        cutoff_str = now.strftime("%Y-%m-%d")
        return [o for o in orders if (o.created_at or "").startswith(cutoff_str)]
    deltas = {"30d": 30, "7d": 7, "24h": 1}
    days = deltas.get(window)
    if days is None:
        return orders
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    return [o for o in orders if (o.created_at or "") >= cutoff]


def _compute_stats(
    strategy_id: str,
    window: str,
    orders: list[V3PaperOrder],
) -> V3Stats:
    pushed    = sum(1 for o in orders if o.pushed)
    open_cnt  = sum(1 for o in orders if o.status in ("OPEN", "FILLED"))
    filled    = sum(1 for o in orders if o.filled_at is not None)
    settled   = sum(1 for o in orders if o.status == "CLOSED" and o.result in ("TP1","TP2","SL","TIMEOUT"))
    tp1       = sum(1 for o in orders if o.result == "TP1")
    tp2       = sum(1 for o in orders if o.result == "TP2")
    sl        = sum(1 for o in orders if o.result == "SL")
    timeout   = sum(1 for o in orders if o.result == "TIMEOUT")
    expired   = sum(1 for o in orders if o.result == "EXPIRED_NOT_FILLED")

    # Win rate denominator: only TP1 + SL (timeout excluded)
    denom = tp1 + sl
    win_rate = (Decimal(tp1) / Decimal(denom) * 100).quantize(Decimal("0.01")) if denom else Decimal("0")

    # Average RR and PnL: only TP1 + SL orders with data
    rr_orders  = [o for o in orders if o.result in ("TP1","SL") and o.rr_realized is not None]
    pnl_orders = [o for o in orders if o.result in ("TP1","SL") and o.pnl_pct is not None]

    avg_rr = (
        sum(o.rr_realized for o in rr_orders) / Decimal(len(rr_orders))
    ).quantize(Decimal("0.01")) if rr_orders else Decimal("0")

    avg_pnl = (
        sum(o.pnl_pct for o in pnl_orders) / Decimal(len(pnl_orders))
    ).quantize(Decimal("0.01")) if pnl_orders else Decimal("0")

    return V3Stats(
        window=window,
        strategy_id=strategy_id,
        signals=len(orders),
        pushed=pushed,
        filled=filled,
        settled=settled,
        tp1=tp1,
        tp2=tp2,
        sl=sl,
        timeout=timeout,
        expired_not_filled=expired,
        open_count=open_cnt,
        win_rate=win_rate,
        avg_rr=avg_rr,
        avg_pnl=avg_pnl,
    )
