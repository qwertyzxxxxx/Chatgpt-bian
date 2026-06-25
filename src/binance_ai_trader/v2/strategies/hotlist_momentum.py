"""V2 Hotlist Momentum Strategy — outputs V2Signal only, no settlement logic.

Copies V1 HotlistWatcher parameters exactly:
  - 24h |change| >= 15%
  - 24h quote_volume >= 5,000,000 USDT
  - EMA20 pullback entry
  - RR >= 2
  - stop <= 5%
  - max 3 signals per scan
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.hotlist.service import HotlistWatcher, HotlistWatcherPolicy
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.v2.signals.repository import V2Signal, V2SignalRepository, make_signal_id
from binance_ai_trader.v2.strategy_registry.repository import V2Strategy

log = logging.getLogger(__name__)

_STRATEGY_ID = "hotlist_momentum_v2"
_DEFAULT_MAX_SIGNALS = 3
_DEFAULT_MIN_RR = Decimal("2")
_DEFAULT_MAX_STOP_PCT = Decimal("5")
_DEFAULT_HOLD_HOURS = 24


class HotlistMomentumV2:
    """V2 strategy wrapper — scans hotlist, emits V2Signals, deduplicates."""

    def __init__(
        self,
        client: BinancePublicClient,
        universe_config: UniverseConfig,
        signal_repo: V2SignalRepository,
        strategy: V2Strategy,
    ) -> None:
        self._client = client
        self._universe_config = universe_config
        self._signal_repo = signal_repo
        self._strategy = strategy

    def scan(self, now: datetime | None = None) -> list[V2Signal]:
        """Run a single scan pass. Returns newly created signals."""
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        params = self._strategy.parameters

        min_move_pct = Decimal(str(params.get("min_move_pct", "15")))
        min_quote_volume = Decimal(str(params.get("min_quote_volume", "5000000")))
        max_signals = int(params.get("max_open_orders", _DEFAULT_MAX_SIGNALS))
        min_rr = Decimal(str(params.get("min_rr", str(_DEFAULT_MIN_RR))))
        max_stop_pct = Decimal(str(params.get("max_stop_pct", str(_DEFAULT_MAX_STOP_PCT))))
        hold_hours = int(params.get("max_hold_hours", _DEFAULT_HOLD_HOURS))

        policy = HotlistWatcherPolicy(
            limit=max_signals,
            min_move_pct=min_move_pct,
            min_quote_volume=min_quote_volume,
            expiry_minutes=hold_hours * 60,
        )
        watcher = HotlistWatcher(self._client, self._universe_config, policy)

        try:
            plans = watcher.watch(generated_at)
        except Exception as exc:
            log.warning("[V2] hotlist scan failed: %s", exc)
            return []

        new_signals: list[V2Signal] = []
        for plan in plans:
            if plan.rr < min_rr:
                log.debug("[V2] %s skipped: rr=%.2f < %.2f", plan.symbol, plan.rr, min_rr)
                continue

            entry = plan.suggested_limit_entry
            stop_pct = abs(entry - plan.stop_loss) / entry * 100
            if stop_pct > max_stop_pct:
                log.debug(
                    "[V2] %s skipped: stop_pct=%.1f%% > %.1f%%",
                    plan.symbol, stop_pct, max_stop_pct,
                )
                continue

            if self._signal_repo.exists_recent(
                _STRATEGY_ID, plan.symbol, plan.direction, hours=24
            ):
                log.debug("[V2] %s/%s dedup skip (24h window)", plan.symbol, plan.direction)
                continue

            signal = V2Signal(
                signal_id=make_signal_id(),
                strategy_id=_STRATEGY_ID,
                symbol=plan.symbol,
                direction=plan.direction,
                entry=plan.suggested_limit_entry,
                stop_loss=plan.stop_loss,
                tp1=plan.tp1,
                tp2=plan.tp2,
                rr=plan.rr,
                reason=plan.reason,
                metadata_json=json.dumps({
                    "sentiment": plan.sentiment,
                    "change_24h_pct": str(plan.change_24h_pct),
                    "quote_volume": str(plan.quote_volume),
                    "ema20_15m": str(plan.ema20_15m),
                    "atr14": str(plan.atr14),
                }),
                created_at=generated_at.isoformat(timespec="seconds"),
            )
            self._signal_repo.save(signal)
            new_signals.append(signal)
            log.info(
                "[V2] signal created: %s %s entry=%s rr=%s",
                signal.symbol, signal.direction, signal.entry, signal.rr,
            )

            if len(new_signals) >= max_signals:
                break

        return new_signals
