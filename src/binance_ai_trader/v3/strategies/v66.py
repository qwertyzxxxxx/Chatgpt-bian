"""V66 Hotlist Strategy — V1-style watchlist (no 15% move requirement).

Unlike V3 hotlist which requires ≥15% 24h move, V66 tracks the top 6
gainers + top 6 losers purely by volume — exactly as V1 did.

Coins enter the watchlist if they have enough volume, and are planned
when a valid entry (EMA20 pullback) exists with stop ≤ 5%.

Strategy ID : hotlist_v66
Signal prefix: V66-YYYYMMDD-NNNNNN
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.hotlist.repository import HotlistWatchlistRepository
from binance_ai_trader.hotlist.watchlist import HotlistWatchlist, HotlistWatchlistPolicy
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.v3.candidates.repository import CandidateInput
from binance_ai_trader.v3.strategies.base import V3Strategy

log = logging.getLogger(__name__)

_STRATEGY_ID      = "hotlist_v66"
_MAX_STOP_PCT     = Decimal("5")
_MIN_RR           = Decimal("2")
_MIN_VOLUME       = Decimal("5000000")
_GAINERS          = 6
_LOSERS           = 6
_MAX_OPP          = 3
_EXPIRY_MIN       = 60
_MAX_TTL_MIN      = 120
_REFRESH_MIN      = 15


class HotlistStrategyV66(V3Strategy):
    """V66: V1-style watchlist — top 6 gainers + top 6 losers, stop ≤ 5%.

    No minimum 24h move percentage required to enter the watchlist.
    Coins are observed for up to 2 hours and planned on every 15m refresh.
    """

    def __init__(
        self,
        client: BinancePublicClient,
        universe_config: UniverseConfig,
        watchlist_db: Path,
    ) -> None:
        self._client          = client
        self._universe_config = universe_config
        self._repo            = HotlistWatchlistRepository(watchlist_db)

    @property
    def strategy_id(self) -> str:
        return _STRATEGY_ID

    def generate_candidates(self, now: datetime | None = None) -> list[CandidateInput]:
        generated_at = self._now(now)

        policy = HotlistWatchlistPolicy(
            gainers=_GAINERS,
            losers=_LOSERS,
            max_opportunities=_MAX_OPP,
            expiry_minutes=_EXPIRY_MIN,
            max_ttl_minutes=_MAX_TTL_MIN,
            refresh_minutes=_REFRESH_MIN,
            min_rr=_MIN_RR,
            max_stop_pct=_MAX_STOP_PCT,
            min_quote_volume=_MIN_VOLUME,
        )
        watcher = HotlistWatchlist(
            self._client, self._repo, self._universe_config, policy
        )

        try:
            plans = watcher.review(generated_at)
        except Exception as exc:
            log.warning("[V66] watchlist review failed: %s", exc)
            return []

        candidates: list[CandidateInput] = []
        for plan in plans:
            entry    = plan.suggested_limit_entry
            stop_pct = abs(entry - plan.stop_loss) / entry * 100

            candidates.append(
                CandidateInput(
                    strategy_id=_STRATEGY_ID,
                    symbol=plan.symbol,
                    direction=plan.direction,
                    entry=str(plan.suggested_limit_entry),
                    sl=str(plan.stop_loss),
                    tp1=str(plan.tp1),
                    tp2=str(plan.tp2),
                    rr=str(plan.rr),
                    confidence=float(plan.rr) / 5.0,
                    stop_pct=float(stop_pct.quantize(Decimal("0.01"))),
                    change_24h=float(plan.change_24h_pct),
                    quote_volume=float(plan.quote_volume),
                    volume_ratio=None,
                    atr=float(plan.atr14),
                    ema20=float(plan.ema20_15m),
                    ema60=None,
                    market_regime=plan.sentiment,
                    reason=plan.reason,
                )
            )

        log.info("[V66] %d candidates generated", len(candidates))
        return candidates

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({
            "strategy_version": "v66",
            "source": "hotlist_watchlist_v1",
        })
        return base
