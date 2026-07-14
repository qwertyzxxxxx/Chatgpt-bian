"""V663 Hotlist Strategy — V662 升级版，趋势判断改为三线排列 EMA10>EMA20>EMA50。

改动 vs V662:
  - 趋势判断: 1h EMA10 > EMA20 > EMA50（多头）/ EMA10 < EMA20 < EMA50（空头）
  - 趋势判断: 4h 同上三线排列
  - 量比: ≥ 1.2x（与V662相同）
  - 其它参数与V662一致 (min_move=5%, max_stop=3%, TTL=90min)

Strategy ID : hotlist_v663
Signal prefix: V663-YYYYMMDD-NNNNNN
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

_STRATEGY_ID           = "hotlist_v663"
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


class HotlistStrategyV663(V3Strategy):
    """V663: V662 趋势升级版 — 三线排列 EMA10>20>50 替代简单价格位置判断。

    相比V662:
    - 1h 需要 EMA10 > EMA20 > EMA50（多头）或反向（空头）
    - 4h 同上三线排列
    - 只接受趋势排列完整的信号，过滤震荡行情

    与V662相同:
    - 量比 ≥ 1.2x
    - 24h 涨跌幅 ≥ 5%
    - 止损距离 ≤ 3%
    - 监控TTL 90分钟
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
            require_triple_ema_1h=True,
            require_triple_ema_4h=False,
            require_trend_aligned_4h=True,
        )
        watcher = HotlistWatchlist(
            self._client, self._repo, self._universe_config, policy
        )

        try:
            plans = watcher.review(generated_at)
        except Exception as exc:
            log.warning("[V663] watchlist review failed: %s", exc)
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

        log.info("[V663] %d candidates generated", len(candidates))
        return candidates

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({
            "strategy_version": "v663",
            "source": "hotlist_watchlist_v663",
        })
        return base
