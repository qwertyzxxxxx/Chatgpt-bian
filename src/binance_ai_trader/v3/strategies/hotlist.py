"""V3 Hotlist Strategy — wraps existing HotlistWatcher, outputs CandidateInput.

Strategy ID: hotlist_momentum_v3
Signal prefix: HOT-YYYYMMDD-NNNNNN

This is the first V3 strategy.  It re-uses the battle-tested HotlistWatcher
from V1 but outputs V3 CandidateInput instead of V2Signal.
Risk, Dedup, Push, Settlement, and Performance are all handled by the
shared V3 Pipeline.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.hotlist.service import HotlistWatcher, HotlistWatcherPolicy
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.v3.candidates.repository import CandidateInput
from binance_ai_trader.v3.strategies.base import V3Strategy

log = logging.getLogger(__name__)

_STRATEGY_ID = "hotlist_momentum_v3"

_DEFAULT_MIN_MOVE_PCT    = Decimal("15")
_DEFAULT_MIN_VOL_USDT    = Decimal("5000000")
_DEFAULT_MAX_SIGNALS     = 3
_DEFAULT_MIN_RR          = Decimal("2")
_DEFAULT_MAX_STOP_PCT    = Decimal("20")
_DEFAULT_HOLD_HOURS      = 24


class HotlistStrategyV3(V3Strategy):
    """V3 Hotlist strategy — same filters as V2, pure V3 output."""

    def __init__(
        self,
        client: BinancePublicClient,
        universe_config: UniverseConfig,
        min_move_pct: Decimal = _DEFAULT_MIN_MOVE_PCT,
        min_quote_volume: Decimal = _DEFAULT_MIN_VOL_USDT,
        max_signals: int = _DEFAULT_MAX_SIGNALS,
        min_rr: Decimal = _DEFAULT_MIN_RR,
        max_stop_pct: Decimal = _DEFAULT_MAX_STOP_PCT,
        hold_hours: int = _DEFAULT_HOLD_HOURS,
    ) -> None:
        self._client = client
        self._universe_config = universe_config
        self._min_move_pct = min_move_pct
        self._min_quote_volume = min_quote_volume
        self._max_signals = max_signals
        self._min_rr = min_rr
        self._max_stop_pct = max_stop_pct
        self._hold_hours = hold_hours

    @property
    def strategy_id(self) -> str:
        return _STRATEGY_ID

    def generate_candidates(self, now: datetime | None = None) -> list[CandidateInput]:
        generated_at = self._now(now)
        policy = HotlistWatcherPolicy(
            limit=self._max_signals,
            min_move_pct=self._min_move_pct,
            min_quote_volume=self._min_quote_volume,
            expiry_minutes=self._hold_hours * 60,
        )
        watcher = HotlistWatcher(self._client, self._universe_config, policy)

        try:
            plans = watcher.watch(generated_at)
        except Exception as exc:
            log.warning("[V3/Hotlist] scan failed: %s", exc)
            return []

        candidates: list[CandidateInput] = []
        for plan in plans:
            if plan.rr < self._min_rr:
                log.debug("[V3/Hotlist] %s skipped: rr=%.2f < %.2f", plan.symbol, plan.rr, self._min_rr)
                continue

            entry = plan.suggested_limit_entry
            stop_pct = abs(entry - plan.stop_loss) / entry * 100
            if stop_pct > self._max_stop_pct:
                log.debug("[V3/Hotlist] %s skipped: stop_pct=%.1f%%", plan.symbol, stop_pct)
                continue

            candidates.append(CandidateInput(
                strategy_id=_STRATEGY_ID,
                symbol=plan.symbol,
                direction=plan.direction,
                entry=str(plan.suggested_limit_entry),
                sl=str(plan.stop_loss),
                tp1=str(plan.tp1),
                tp2=str(plan.tp2),
                rr=str(plan.rr),
                confidence=float(plan.rr) / 5.0,          # simple proxy: rr/5
                stop_pct=float(stop_pct.quantize(Decimal("0.01"))),
                change_24h=float(plan.change_24h_pct),
                quote_volume=float(plan.quote_volume),
                volume_ratio=None,
                atr=float(plan.atr14),
                ema20=float(plan.ema20_15m),
                ema60=None,
                market_regime=plan.sentiment,
                reason=plan.reason,
            ))

            if len(candidates) >= self._max_signals:
                break

        log.info("[V3/Hotlist] %d candidates generated", len(candidates))
        return candidates

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({
            "strategy_version": "v3",
            "source": "hotlist_watcher",
        })
        return base
