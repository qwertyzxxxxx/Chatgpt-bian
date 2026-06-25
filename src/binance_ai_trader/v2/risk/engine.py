"""V2 Risk Engine — gates signal → paper order creation.

Phase 1A checks:
  1. max_open_orders per strategy (from strategy parameters_json)
  2. same symbol + direction already OPEN or FILLED (no duplicate position)
  3. symbol in strategy blacklist
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from binance_ai_trader.v2.paper_portfolio.repository import V2PaperOrderRepository
from binance_ai_trader.v2.signals.repository import V2Signal
from binance_ai_trader.v2.strategy_registry.repository import V2Strategy

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reason: str


class V2RiskEngine:
    """Stateless risk gate: call check() before creating a paper order."""

    def __init__(self, order_repo: V2PaperOrderRepository) -> None:
        self._order_repo = order_repo

    def check(self, signal: V2Signal, strategy: V2Strategy) -> RiskDecision:
        """Return RiskDecision(allowed=True) if the signal passes all gates."""
        if signal.symbol in strategy.blacklist:
            return RiskDecision(False, f"symbol {signal.symbol} is blacklisted")

        if self._order_repo.exists_open_for_symbol_direction(
            signal.strategy_id, signal.symbol, signal.direction
        ):
            return RiskDecision(
                False,
                f"duplicate open {signal.direction} position for {signal.symbol}",
            )

        open_count = self._order_repo.count_open_by_strategy(signal.strategy_id)
        if open_count >= strategy.max_open_orders:
            return RiskDecision(
                False,
                f"max_open_orders={strategy.max_open_orders} reached "
                f"(currently {open_count} open)",
            )

        return RiskDecision(True, "ok")
