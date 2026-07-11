"""V662 Hotlist Strategy — V66 升级版，加入量能+4h/1h趋势过滤。

改动 vs V66:
  - 最小24h涨跌幅: ≥5% (V66=0%)
  - 最大止损距离: ≤3% (V66=5%)
  - 量比门槛: 当前15m量比 ≥ 1.2x 均量 (V66=无)
  - 1h趋势对齐: 价格必须在1h EMA20正确一侧 (V66=无)
  - 4h趋势对齐: 价格必须在4h EMA50正确一侧 (V66=无)
  - 监控TTL: 90分钟 (V66=120分钟)

Strategy ID : hotlist_v662
Signal prefix: V662-YYYYMMDD-NNNNNN
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.hotlist.pg_watchlist_repo import V66WatchlistPgRepository
from binance_ai_trader.hotlist.watchlist import HotlistWatchlist, HotlistWatchlistPolicy
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.v3.candidates.repository import CandidateInput
from binance_ai_trader.v3.strategies.base import V3Strategy

log = logging.getLogger(__name__)

_STRATEGY_ID           = "hotlist_v662"
_MAX_STOP_PCT          = Decimal("3")
_MIN_RR                = Decimal("2")
_MIN_VOLUME            = Decimal("5000000")
_MIN_MOVE_PCT          = Decimal("5")
_MIN_VOL_RATIO         = Decimal("1.2")
_GAINERS               = 6
_LOSERS                = 6
_MAX_OPP               = 3
_EXPIRY_MIN            = 60
_MAX_TTL_MIN           = 90
_REFRESH_MIN           = 15


class HotlistStrategyV662(V3Strategy):
    """V662: V66 加强版 — 量比+1h趋势+4h趋势三道硬门槛，止损收紧至3%。

    相比V66:
    - 需要真实动量 (≥5% 24h move)
    - 需要量能配合 (volume_ratio ≥ 1.2x)
    - 需要1h + 4h趋势对齐，避免逆势入场
    - 止损距离≤3%，提升信号质量
    - 更短的观察TTL (90分钟) 避免过期信号
    """

    def __init__(
        self,
        client: BinancePublicClient,
        universe_config: UniverseConfig,
    ) -> None:
        self._client          = client
        self._universe_config = universe_config
        self._repo            = V66WatchlistPgRepository()

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
            min_move_pct=_MIN_MOVE_PCT,
            min_volume_ratio=_MIN_VOL_RATIO,
            require_trend_aligned_1h=True,
            require_trend_aligned_4h=True,
        )
        watcher = HotlistWatchlist(
            self._client, self._repo, self._universe_config, policy
        )

        try:
            plans = watcher.review(generated_at)
        except Exception as exc:
            log.warning("[V662] watchlist review failed: %s", exc)
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
                    volume_ratio=float(plan.volume_ratio_15m),
                    atr=float(plan.atr14),
                    ema20=float(plan.ema20_15m),
                    ema60=None,
                    market_regime=plan.sentiment,
                    reason=plan.reason,
                )
            )

        log.info("[V662] %d candidates generated", len(candidates))
        return candidates

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({
            "strategy_version": "v662",
            "source": "hotlist_watchlist_v662",
        })
        return base
