"""V3 Runtime Settings — live-adjustable dedup/position-limit overrides (PostgreSQL).

Lets an admin tune `dedup_hours` and `max_open_orders` per strategy via the
Telegram `/setlimit` command, without redeploying. Values are read fresh on
every scan cycle by the task closures in `v3/runner/tasks.py`.

A NULL column in `v3_runtime_settings` means "no override — use the hardcoded
default the strategy was deployed with".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from binance_ai_trader.v3.storage.pg import get_conn

log = logging.getLogger(__name__)

# Same discrete windows the dedup engine itself accepts (see v3/dedup/engine.py).
VALID_DEDUP_HOURS = frozenset({4, 12, 24, 48})
MIN_MAX_OPEN_ORDERS = 1
MAX_MAX_OPEN_ORDERS = 50

# Strategy id + defaults the bot is deployed with (single source of truth —
# run_server.py and tasks.py should not hardcode these again).
V3_STRATEGY_ID = "hotlist_momentum_v3"
V66_STRATEGY_ID = "hotlist_v66"

STRATEGY_ALIASES: dict[str, str] = {
    "v3": V3_STRATEGY_ID,
    "v66": V66_STRATEGY_ID,
}

DEFAULTS: dict[str, dict[str, int]] = {
    V3_STRATEGY_ID: {"dedup_hours": 24, "max_open_orders": 10},
    V66_STRATEGY_ID: {"dedup_hours": 24, "max_open_orders": 5},
}


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    strategy_id: str
    dedup_hours: int | None = None
    max_open_orders: int | None = None
    updated_at: str | None = None
    updated_by: str | None = None
    live_enabled: bool | None = None
    notional_usdt: str | None = None


# Hardcoded fallback live-trading defaults, used only when there is no DB row
# yet (fresh deploy). Per the 2026-07 rollout: V3 live trading is OFF (paper
# continues), V66 goes live with a 2000 USDT position size.
LIVE_DEFAULTS: dict[str, dict[str, object]] = {
    V3_STRATEGY_ID:  {"live_enabled": False, "notional_usdt": "1000"},
    V66_STRATEGY_ID: {"live_enabled": True,  "notional_usdt": "2000"},
}


class V3RuntimeSettingsRepository:
    """Reads/writes per-strategy overrides. Safe to instantiate freely (stateless)."""

    def get(self, strategy_id: str) -> RuntimeSettings:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT dedup_hours, max_open_orders, updated_at, updated_by,
                              live_enabled, notional_usdt
                       FROM v3_runtime_settings WHERE strategy_id=%s""",
                    (strategy_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return RuntimeSettings(strategy_id=strategy_id)
        return RuntimeSettings(
            strategy_id=strategy_id,
            dedup_hours=row[0],
            max_open_orders=row[1],
            updated_at=row[2],
            updated_by=row[3],
            live_enabled=row[4],
            notional_usdt=str(row[5]) if row[5] is not None else None,
        )

    def resolve_live(self, strategy_id: str) -> tuple[bool, "Decimal"]:
        """Return the effective (live_enabled, notional_usdt) for a strategy —
        DB override if set, otherwise the hardcoded rollout default."""
        from decimal import Decimal

        defaults = LIVE_DEFAULTS.get(strategy_id, {"live_enabled": False, "notional_usdt": "1000"})
        s = self.get(strategy_id)
        live_enabled = s.live_enabled if s.live_enabled is not None else bool(defaults["live_enabled"])
        notional = Decimal(s.notional_usdt) if s.notional_usdt is not None else Decimal(str(defaults["notional_usdt"]))
        return live_enabled, notional

    def set_live_enabled(self, strategy_id: str, enabled: bool, updated_by: str | None = None) -> None:
        self._upsert(strategy_id, live_enabled=enabled, updated_by=updated_by)

    def set_notional_usdt(self, strategy_id: str, notional: "Decimal | str | int", updated_by: str | None = None) -> None:
        from decimal import Decimal

        value = Decimal(str(notional))
        if value <= 0:
            raise ValueError(f"notional_usdt 必须 > 0，收到 {value}")
        self._upsert(strategy_id, notional_usdt=value, updated_by=updated_by)

    def resolve(self, strategy_id: str) -> tuple[int, int]:
        """Return the effective (dedup_hours, max_open_orders) for a strategy —
        live override if set, otherwise the hardcoded default."""
        defaults = DEFAULTS.get(strategy_id, {"dedup_hours": 24, "max_open_orders": 5})
        s = self.get(strategy_id)
        dedup_hours = s.dedup_hours if s.dedup_hours is not None else defaults["dedup_hours"]
        max_open_orders = (
            s.max_open_orders if s.max_open_orders is not None else defaults["max_open_orders"]
        )
        return dedup_hours, max_open_orders

    def set_dedup_hours(self, strategy_id: str, hours: int, updated_by: str | None = None) -> None:
        if hours not in VALID_DEDUP_HOURS:
            raise ValueError(f"dedup_hours 必须是 {sorted(VALID_DEDUP_HOURS)} 之一，收到 {hours}")
        self._upsert(strategy_id, dedup_hours=hours, updated_by=updated_by)

    def set_max_open_orders(self, strategy_id: str, value: int, updated_by: str | None = None) -> None:
        if not (MIN_MAX_OPEN_ORDERS <= value <= MAX_MAX_OPEN_ORDERS):
            raise ValueError(
                f"max_open_orders 必须在 {MIN_MAX_OPEN_ORDERS}-{MAX_MAX_OPEN_ORDERS} 之间，收到 {value}"
            )
        self._upsert(strategy_id, max_open_orders=value, updated_by=updated_by)

    def reset(self, strategy_id: str) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM v3_runtime_settings WHERE strategy_id=%s", (strategy_id,))
            conn.commit()
        finally:
            conn.close()

    def _upsert(
        self,
        strategy_id: str,
        dedup_hours: int | None = None,
        max_open_orders: int | None = None,
        live_enabled: bool | None = None,
        notional_usdt: "object | None" = None,
        updated_by: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO v3_runtime_settings
                           (strategy_id, dedup_hours, max_open_orders, updated_at, updated_by,
                            live_enabled, notional_usdt)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (strategy_id) DO UPDATE SET
                           dedup_hours     = COALESCE(EXCLUDED.dedup_hours, v3_runtime_settings.dedup_hours),
                           max_open_orders = COALESCE(EXCLUDED.max_open_orders, v3_runtime_settings.max_open_orders),
                           live_enabled    = COALESCE(EXCLUDED.live_enabled, v3_runtime_settings.live_enabled),
                           notional_usdt   = COALESCE(EXCLUDED.notional_usdt, v3_runtime_settings.notional_usdt),
                           updated_at      = EXCLUDED.updated_at,
                           updated_by      = EXCLUDED.updated_by""",
                    (strategy_id, dedup_hours, max_open_orders, now, updated_by,
                     live_enabled, notional_usdt),
                )
            conn.commit()
        finally:
            conn.close()
        log.info(
            "[V3Settings] %s updated: dedup_hours=%s max_open_orders=%s live_enabled=%s notional_usdt=%s by=%s",
            strategy_id, dedup_hours, max_open_orders, live_enabled, notional_usdt, updated_by,
        )
