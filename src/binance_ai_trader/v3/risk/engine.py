"""V3 Risk Engine — unified gate for all strategies.

All strategies call V3RiskEngine.check() before a candidate is accepted.
This engine is stateless: it reads from the DB on each call.

Checks (in order):
  1. Symbol blacklist (per-strategy config)
  2. Market regime block (per-strategy config)
  3. Duplicate open/filled position (symbol + direction already in v3_paper_orders)
  4. Max open orders per strategy (v3_paper_orders)

Returns RiskDecision(allowed, reason).
Only reads from v3_* tables — no V1/V2 dependencies.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Per-strategy risk parameters."""
    strategy_id: str
    max_open_orders: int = 5
    blacklist: frozenset[str] = frozenset()
    blocked_regimes: frozenset[str] = frozenset()


class V3RiskEngine:
    """Stateless risk gate.  Instantiate once; call check() per candidate."""

    def __init__(self, db_path: Path | str) -> None:
        self._db = str(db_path)

    def check(
        self,
        strategy_id: str,
        symbol: str,
        direction: str,
        config: RiskConfig | None = None,
        market_regime: str | None = None,
    ) -> RiskDecision:
        cfg = config or self._load_config(strategy_id)

        if symbol in cfg.blacklist:
            return RiskDecision(False, f"symbol {symbol} is blacklisted")

        if market_regime and market_regime in cfg.blocked_regimes:
            return RiskDecision(
                False, f"market regime '{market_regime}' is blocked for this strategy"
            )

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
        with sqlite3.connect(self._db) as conn:
            if not _table_exists(conn, "v3_paper_orders"):
                return False
            row = conn.execute(
                """SELECT 1 FROM v3_paper_orders
                   WHERE strategy_id=? AND symbol=? AND direction=?
                     AND status IN ('OPEN','FILLED')
                   LIMIT 1""",
                (strategy_id, symbol, direction),
            ).fetchone()
        return row is not None

    def _count_open(self, strategy_id: str) -> int:
        with sqlite3.connect(self._db) as conn:
            if not _table_exists(conn, "v3_paper_orders"):
                return 0
            row = conn.execute(
                "SELECT COUNT(*) FROM v3_paper_orders WHERE strategy_id=? AND status IN ('OPEN','FILLED')",
                (strategy_id,),
            ).fetchone()
        return row[0] if row else 0

    def _load_config(self, strategy_id: str) -> RiskConfig:
        """Load config from v3_strategies table; default if not found."""
        with sqlite3.connect(self._db) as conn:
            if _table_exists(conn, "v3_strategies"):
                row = conn.execute(
                    "SELECT parameters_json FROM v3_strategies WHERE strategy_id=?",
                    (strategy_id,),
                ).fetchone()
                if row:
                    params = json.loads(row[0] or "{}")
                    return RiskConfig(
                        strategy_id=strategy_id,
                        max_open_orders=int(params.get("max_open_orders", 5)),
                        blacklist=frozenset(params.get("blacklist", [])),
                        blocked_regimes=frozenset(params.get("blocked_regimes", [])),
                    )
        return RiskConfig(strategy_id=strategy_id)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None
