"""V664 Hotlist Strategy — 精准回踩 + 量缩确认，多空双向，持仓短。

策略方案:
  - 趋势: 1h + 4h EMA10 > EMA20 > EMA50 (多头) / 反向 (空头)
  - 动量: 24h 涨跌幅 ≥ 5%
  - 精准入场: 当前价在15m EMA20 ±1.5% 以内（已回踩到位，限价单快速成交）
  - 量缩确认: 量比 < 1.0（回踩/反弹时逆势力量弱）
  - 止损: ≤ 2.5%（更紧，持仓更短）
  - TTL: 60分钟（不触发即放弃）
  - TP2 = 2×risk (1:2 RR)

胜率逻辑:
  三线排列确认趋势 + 价格已回踩到EMA20 + 量缩 = 三重过滤
  任何一项缺失都不进场，信号少但质量高

Strategy ID : hotlist_v664
Signal prefix: HOT-YYYYMMDD-NNNNNN
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

_STRATEGY_ID           = "hotlist_v664"
_MAX_STOP_PCT          = Decimal("2.5")
_MIN_RR                = Decimal("2")
_MIN_VOLUME            = Decimal("5000000")
_MIN_MOVE_PCT          = Decimal("5")
_MAX_ENTRY_DIST_PCT    = Decimal("1.5")   # 当前价必须在EMA20 1.5% 以内
_GAINERS               = 6
_LOSERS                = 6
_MAX_OPP               = 3
_EXPIRY_MIN            = 60
_MAX_TTL_MIN           = 60               # 比V663更短的TTL
_REFRESH_MIN           = 15


class HotlistStrategyV664(V3Strategy):
    """V664: 精准回踩做法 — 三线排列 + EMA20 到位 + 量缩，多空双向。

    关键改进 vs V663:
    - 要求当前价已回踩到 EMA20 ±1.5%（不等，直接在位置上）
    - 要求回踩时量比 < 1.0（逆势力量弱，趋势恢复概率高）
    - 止损收紧至 2.5%（更快到达TP或被止损，持仓更短）
    - TTL 缩短至 60 分钟（严格时效，不犹豫就不做）
    - 多空双向（趋势决定方向，策略不预判市场）
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
            require_triple_ema_1h=True,
            require_triple_ema_4h=True,
            max_entry_distance_pct=_MAX_ENTRY_DIST_PCT,
            require_low_volume=True,
        )
        watcher = HotlistWatchlist(
            self._client, self._repo, self._universe_config, policy
        )

        try:
            plans = watcher.review(generated_at)
        except Exception as exc:
            log.warning("[V664] watchlist review failed: %s", exc)
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

        log.info("[V664] %d candidates generated", len(candidates))
        return candidates

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({
            "strategy_version": "v664",
            "source": "hotlist_watchlist_v664",
        })
        return base
