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
_DEFAULT_MAX_STOP_PCT    = Decimal("20")     # paper-trading ceiling
_DEFAULT_HOLD_HOURS      = 24
_DEFAULT_POOL_SIZE       = 15

# Mirrors LiveMirrorEngine._LIVE_MAX_STOP_PCT — used only to *rank* candidates
# (live-eligible ones first); the live engine independently re-enforces this
# limit before placing a real order.
_LIVE_MAX_STOP_PCT = Decimal("8")


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
        pool_size: int = _DEFAULT_POOL_SIZE,
    ) -> None:
        self._client = client
        self._universe_config = universe_config
        self._min_move_pct = min_move_pct
        self._min_quote_volume = min_quote_volume
        self._max_signals = max_signals
        self._min_rr = min_rr
        self._max_stop_pct = max_stop_pct
        self._hold_hours = hold_hours
        self._pool_size = pool_size

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
            # 1) Build a wide POOL (top N by |24h move|, unchanged eligibility
            #    filters) — this is only a coarse pre-filter, NOT the final
            #    selection.
            pool = watcher.candidates_pool(self._pool_size)
            # 2) Compute full entry/SL/TP/RR plans for EVERY pool member (no
            #    early break) so quality-based ranking has full information.
            plans = watcher.plan_all(pool, generated_at)
        except Exception as exc:
            log.warning("[V3/Hotlist] scan failed: %s", exc)
            return []

        scored: list[dict] = []
        for plan in plans:
            entry = plan.suggested_limit_entry
            stop_pct = abs(entry - plan.stop_loss) / entry * 100

            if plan.rr < self._min_rr:
                log.info("[V3/Hotlist] %s SKIP rr=%.2f<%.2f stop=%.1f%%", plan.symbol, plan.rr, self._min_rr, stop_pct)
                continue

            if stop_pct > self._max_stop_pct:
                log.info("[V3/Hotlist] %s SKIP stop=%.1f%%>%.1f%%(纸面上限)", plan.symbol, stop_pct, self._max_stop_pct)
                continue

            scored.append({
                "plan": plan,
                "stop_pct": stop_pct,
                "live_eligible": stop_pct <= _LIVE_MAX_STOP_PCT,
                "trend_aligned": plan.trend_aligned,
            })

        # 3) Quality ranking — NOT by |24h move|. Priority order:
        #      a) live_eligible (stop<=8%) first
        #      b) tighter stop_pct first
        #      c) higher quote_volume first
        #      d) 1h trend-direction match first
        #      e) |24h move| only as a last-resort tiebreaker
        scored.sort(key=lambda s: (
            0 if s["live_eligible"] else 1,
            float(s["stop_pct"]),
            -float(s["plan"].quote_volume),
            0 if s["trend_aligned"] else 1,
            -abs(float(s["plan"].change_24h_pct)),
        ))

        selected = scored[: self._max_signals]
        selected_symbols = {s["plan"].symbol for s in selected}

        candidates: list[CandidateInput] = []
        for s in selected:
            plan = s["plan"]
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
                stop_pct=float(s["stop_pct"].quantize(Decimal("0.01"))),
                change_24h=float(plan.change_24h_pct),
                quote_volume=float(plan.quote_volume),
                volume_ratio=None,
                atr=float(plan.atr14),
                ema20=float(plan.ema20_15m),
                ema60=None,
                market_regime=plan.sentiment,
                reason=plan.reason,
            ))

        self._save_debug_snapshot(pool, scored, selected_symbols, generated_at)

        log.info(
            "[V3/Hotlist] pool=%d computed=%d live_eligible=%d selected=%d",
            len(pool), len(scored), sum(1 for s in scored if s["live_eligible"]), len(candidates),
        )
        return candidates

    def _save_debug_snapshot(self, pool, scored, selected_symbols, generated_at) -> None:
        """Persist ranking diagnostics for the /v4debug Telegram command. Best-effort."""
        try:
            from binance_ai_trader.v3.debug.repository import ScanDebugRepository, ScanDebugSnapshot

            def _row(s: dict) -> dict:
                plan = s["plan"]
                return {
                    "symbol": plan.symbol,
                    "direction": plan.direction,
                    "stop_pct": float(s["stop_pct"].quantize(Decimal("0.01"))),
                    "live_eligible": s["live_eligible"],
                    "trend_aligned": s["trend_aligned"],
                    "quote_volume": float(plan.quote_volume),
                    "change_24h": float(plan.change_24h_pct),
                    "selected": plan.symbol in selected_symbols,
                }

            top10 = [_row(s) for s in scored[:10]]
            crowded_out = [_row(s) for s in scored if s["plan"].symbol not in selected_symbols][:10]

            ScanDebugRepository().save(ScanDebugSnapshot(
                strategy_id=_STRATEGY_ID,
                created_at=generated_at.isoformat(timespec="seconds"),
                pool_size=len(pool),
                computed_count=len(scored),
                live_eligible_count=sum(1 for s in scored if s["live_eligible"]),
                top10=top10,
                crowded_out=crowded_out,
            ))
        except Exception:
            log.exception("[V3/Hotlist] failed to save scan debug snapshot")

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({
            "strategy_version": "v3",
            "source": "hotlist_watcher",
        })
        return base
