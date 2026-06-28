"""V2 Performance Engine — single source of truth, reads only v2_paper_orders + v2_signals.

Win-rate denominator: TP1 + TP2 + SL only.
OPEN, FILLED, EXPIRED_NOT_FILLED, TIMEOUT are excluded from the denominator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.v2.paper_portfolio.repository import V2PaperOrderRepository
from binance_ai_trader.v2.signals.repository import V2SignalRepository

_WIN_RESULTS = frozenset({"TP1", "TP2"})
_DENOMINATOR_RESULTS = frozenset({"TP1", "TP2", "SL"})


def _today_prefix() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class V2Performance:
    strategy_id: str | None
    signals: int
    pushed: int
    orders: int
    filled: int
    not_filled: int
    open_count: int
    settled: int
    tp1: int
    tp2: int
    sl: int
    expired_not_filled: int
    win_rate: Decimal
    avg_rr: Decimal
    avg_pnl: Decimal


@dataclass(frozen=True, slots=True)
class V2TodayStats:
    strategy_id: str | None
    signals: int
    pushed: int
    filled: int
    settled: int
    tp1: int
    tp2: int
    sl: int


class V2PerformanceCalculator:
    """Calculates performance stats from v2_paper_orders and v2_signals."""

    def __init__(
        self,
        repo: V2PaperOrderRepository,
        signal_repo: V2SignalRepository | None = None,
    ) -> None:
        self._repo = repo
        self._signal_repo = signal_repo

    def _signal_count(self, strategy_id: str | None, cutoff: str | None = None) -> int:
        if self._signal_repo is None:
            return 0
        sigs = self._signal_repo.load_all()
        if strategy_id:
            sigs = [s for s in sigs if s.strategy_id == strategy_id]
        if cutoff:
            sigs = [s for s in sigs if s.created_at >= cutoff]
        return len(sigs)

    def calculate(self, strategy_id: str | None = None) -> V2Performance:
        all_orders = self._repo.load_all()
        if strategy_id:
            all_orders = [o for o in all_orders if o.strategy_id == strategy_id]

        total    = len(all_orders)
        pushed   = sum(1 for o in all_orders if o.pushed)
        filled   = sum(1 for o in all_orders if o.status not in ("OPEN",) and o.result != "EXPIRED_NOT_FILLED")
        not_filled = sum(1 for o in all_orders if o.result == "EXPIRED_NOT_FILLED")
        open_count = sum(1 for o in all_orders if o.status in ("OPEN", "FILLED"))
        tp1      = sum(1 for o in all_orders if o.result == "TP1")
        tp2      = sum(1 for o in all_orders if o.result == "TP2")
        sl       = sum(1 for o in all_orders if o.result == "SL")
        expired  = sum(1 for o in all_orders if o.result == "EXPIRED_NOT_FILLED")
        settled  = tp1 + tp2 + sl + expired

        denom_orders = [o for o in all_orders if o.result in _DENOMINATOR_RESULTS]
        win_orders   = [o for o in denom_orders if o.result in _WIN_RESULTS]

        denom = len(denom_orders)
        win_rate = (
            Decimal(len(win_orders)) / Decimal(denom) * Decimal("100")
            if denom > 0 else Decimal("0")
        )

        rr_vals  = [o.rr_realized for o in denom_orders if o.rr_realized is not None]
        avg_rr   = sum(rr_vals, Decimal("0")) / Decimal(len(rr_vals)) if rr_vals else Decimal("0")

        pnl_vals = [o.pnl_pct for o in denom_orders if o.pnl_pct is not None]
        avg_pnl  = sum(pnl_vals, Decimal("0")) / Decimal(len(pnl_vals)) if pnl_vals else Decimal("0")

        signals = self._signal_count(strategy_id)

        return V2Performance(
            strategy_id=strategy_id,
            signals=signals,
            pushed=pushed,
            orders=total,
            filled=filled,
            not_filled=not_filled,
            open_count=open_count,
            settled=settled,
            tp1=tp1,
            tp2=tp2,
            sl=sl,
            expired_not_filled=expired,
            win_rate=win_rate.quantize(Decimal("0.01")),
            avg_rr=avg_rr.quantize(Decimal("0.01")),
            avg_pnl=avg_pnl.quantize(Decimal("0.01")),
        )

    def calculate_today(self, strategy_id: str | None = None) -> V2TodayStats:
        today = _today_prefix()
        all_orders = self._repo.load_all()
        if strategy_id:
            all_orders = [o for o in all_orders if o.strategy_id == strategy_id]

        created_today = [o for o in all_orders if (o.created_at or "").startswith(today)]
        filled_today  = [o for o in all_orders if (o.filled_at  or "").startswith(today)]
        closed_today  = [o for o in all_orders if (o.closed_at  or "").startswith(today)]

        pushed = sum(1 for o in created_today if o.pushed)
        tp1    = sum(1 for o in closed_today  if o.result == "TP1")
        tp2    = sum(1 for o in closed_today  if o.result == "TP2")
        sl     = sum(1 for o in closed_today  if o.result == "SL")

        signals = self._signal_count(strategy_id, cutoff=today)

        return V2TodayStats(
            strategy_id=strategy_id,
            signals=signals,
            pushed=pushed,
            filled=len(filled_today),
            settled=len(closed_today),
            tp1=tp1,
            tp2=tp2,
            sl=sl,
        )
