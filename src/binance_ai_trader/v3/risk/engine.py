"""V3 Risk Engine — unified gate for all strategies (PostgreSQL backend).

Checks (in order):
  1. Symbol blacklist
  2. Market regime block
  3. Duplicate open/filled position (v3_paper_orders in PostgreSQL)
  4. Max open orders per strategy
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from binance_ai_trader.v3.storage.pg import get_conn

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RiskConfig:
    strategy_id: str
    max_open_orders: int = 5
    blacklist: frozenset[str] = frozenset()
    blocked_regimes: frozenset[str] = frozenset()


class V3RiskEngine:
    def __init__(self, db_path: Path | str = None) -> None:
        pass

    def check(
        self,
        strategy_id: str,
        symbol: str,
        direction: str,
        config: RiskConfig | None = None,
        market_regime: str | None = None,
    ) -> RiskDecision:
        cfg = config or RiskConfig(strategy_id=strategy_id)

        if symbol in cfg.blacklist:
            return RiskDecision(False, f"symbol {symbol} is blacklisted")

        if market_regime and market_regime in cfg.blocked_regimes:
            return RiskDecision(False, f"market regime '{market_regime}' blocked")

        if self._has_open_position(strategy_id, symbol, direction):
            return RiskDecision(
                False,
                f"duplicate open {direction} position for {symbol} in {strategy_id}",
            )

        open_count = self._count_open(strategy_id)
        if open_count >= cfg.max_open_orders:
            return RiskDecision(
                False,
                f"max_open_orders={cfg.max_open_orders} reached ({open_count} open)",
            )

        return RiskDecision(True, "ok")

    def _has_open_position(self, strategy_id: str, symbol: str, direction: str) -> bool:
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

    def _count_open(self, strategy_id: str) -> int:
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
