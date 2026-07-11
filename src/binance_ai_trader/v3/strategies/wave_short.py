"""Wave Short Breakdown — 放量跌破反抽做空。

核心逻辑:
  1D EMA20 < EMA60 (大方向空头)
  → 4H EMA20 < EMA60 且价格 < 4H EMA60 (中期空头)
  → 1H 放量跌破平台 (VolumeRatio ≥ 1.5，收盘 < PlatformLow -5% 以内)
  → 进入 SHORT_WATCH 观察区 (最长 8 小时)
  → 15M 缩量反抽平台 + 重新收盘 < PlatformLow + 放量阴线 → 入场做空

Strategy ID : wave_short
Hold        : 48 小时
RR          : 1:2 (TP2 = Entry - 2×Risk)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.v3.candidates.repository import CandidateInput
from binance_ai_trader.v3.strategies.base import V3Strategy
from binance_ai_trader.v3.strategies.wave_common import (
    clamp_stop, ema, platform, swing_high, top_n_usdt_symbols, vol_ma, vol_ratio,
)
from binance_ai_trader.v3.strategies.wave_watchlist_repo import WaveWatchlistRepo

log = logging.getLogger(__name__)

_STRATEGY_ID    = "wave_short"
_PLATFORM_BARS  = 20
_1H_VOL_THRESH  = Decimal("1.5")
_15M_VOL_THRESH = Decimal("1.5")
_RETEST_TOL     = Decimal("0.01")   # PlatformLow ±1%
_MAX_OVERSHOOT  = Decimal("0.05")   # 收盘最多比PlatformLow低5%
_WATCH_HOURS    = 8
_TOP_N          = 100


class WaveShortStrategy(V3Strategy):
    """放量跌破反抽做空。"""

    def __init__(
        self,
        client: BinancePublicClient,
        universe_config: UniverseConfig,
        db_path: Path,
    ) -> None:
        self._client  = client
        self._db_path = db_path
        self._repo    = WaveWatchlistRepo(db_path)

    @property
    def strategy_id(self) -> str:
        return _STRATEGY_ID

    # ── main entry ────────────────────────────────────────────────────────

    def generate_candidates(self, now: datetime | None = None) -> list[CandidateInput]:
        now = self._now(now)

        self._repo.expire_old(now)

        candidates: list[CandidateInput] = []
        for item in self._repo.get_active(_STRATEGY_ID):
            try:
                if self._should_invalidate(item):
                    self._repo.invalidate(item.watch_id)
                    log.debug("[wave_short] 失效: %s (%s)", item.symbol, item.watch_id)
                    continue
                entry = self._check_15m_entry(item)
                if entry:
                    self._repo.mark_entered(item.watch_id)
                    candidates.append(entry)
            except Exception:
                log.exception("[wave_short] 观察区检查异常: %s", item.symbol)

        try:
            universe = top_n_usdt_symbols(self._client, _TOP_N)
        except Exception:
            log.exception("[wave_short] 获取 Top-100 失败，跳过本轮扫描")
            return candidates

        watching = {item.symbol for item in self._repo.get_active(_STRATEGY_ID)}
        for symbol in universe:
            if symbol in watching:
                continue
            try:
                self._scan_symbol(symbol, now)
            except Exception:
                log.debug("[wave_short] 扫描异常: %s", symbol)

        log.info("[wave_short] %d candidates", len(candidates))
        return candidates

    # ── 扫描单币跌破 ──────────────────────────────────────────────────────

    def _scan_symbol(self, symbol: str, now: datetime) -> None:
        # 1D 方向
        d1 = self._client.klines(symbol, "1d", limit=65)
        if len(d1) < 60:
            return
        closes_1d = tuple(k.close for k in d1)
        if ema(closes_1d, 20) >= ema(closes_1d, 60):
            return   # 1D 不是空头排列

        # 4H 趋势
        h4 = self._client.klines(symbol, "4h", limit=65)
        if len(h4) < 60:
            return
        closes_4h = tuple(k.close for k in h4)
        ema20_4h  = ema(closes_4h, 20)
        ema60_4h  = ema(closes_4h, 60)
        if ema20_4h >= ema60_4h:
            return
        cur_price = h4[-1].close
        if cur_price > ema60_4h:
            return   # 价格仍在 4H EMA60 上方，不追空
        last_4h_drop = (h4[-1].open - h4[-1].close) / h4[-1].open
        if last_4h_drop > Decimal("0.12"):
            return   # 最近4H跌幅超12%，避免追空

        # 1H 跌破
        h1 = self._client.klines(symbol, "1h", limit=_PLATFORM_BARS + 3)
        if len(h1) < _PLATFORM_BARS + 2:
            return
        cur_h1 = h1[-1]
        ph, pl  = platform(h1, lookback=_PLATFORM_BARS)
        vr      = vol_ratio(h1, period=_PLATFORM_BARS)

        if cur_h1.close >= pl:
            return   # 未跌破平台低点
        if cur_h1.close > cur_h1.open:
            return   # 阳线不算有效跌破
        overshoot = (pl - cur_h1.close) / pl
        if overshoot > _MAX_OVERSHOOT:
            return   # 收盘比平台低点低出 >5%，已过度延伸
        if vr < _1H_VOL_THRESH:
            return   # 量比不足

        close_time_iso = datetime.fromtimestamp(
            cur_h1.close_time_ms / 1000, tz=UTC
        ).isoformat(timespec="seconds")

        self._repo.add(
            symbol=symbol,
            strategy_id=_STRATEGY_ID,
            direction="SHORT",
            platform_high=ph,
            platform_low=pl,
            breakout_close=cur_h1.close,
            breakout_vol_ratio=vr,
            triggered_at=close_time_iso,
            triggered_at_ms=cur_h1.close_time_ms,
            watch_hours=_WATCH_HOURS,
        )
        log.info(
            "[wave_short] 新跌破 %s  PL=%.6f vr=%.2f",
            symbol, float(pl), float(vr),
        )

    # ── 观察区失效判断 ────────────────────────────────────────────────────

    def _should_invalidate(self, item) -> bool:
        try:
            h1 = self._client.klines(item.symbol, "1h", limit=12)
        except Exception:
            return False

        closes_1h = tuple(k.close for k in h1)
        ema20_1h  = ema(closes_1h, min(20, len(closes_1h)))
        pl        = item.platform_low

        for k in h1:
            if k.close_time_ms <= item.triggered_at_ms:
                continue
            # 条件1: 收盘重新站回平台低点上方
            if k.close > pl:
                return True
            # 条件2: 收盘站上 1H EMA20
            if k.close > ema20_1h:
                return True
            # 条件3: 放量阳线
            if k.close > k.open:
                h1_vr = vol_ratio(h1, period=min(20, len(h1) - 1))
                if h1_vr >= _1H_VOL_THRESH:
                    return True
        return False

    # ── 15M 入场检查 ──────────────────────────────────────────────────────

    def _check_15m_entry(self, item) -> CandidateInput | None:
        klines_15m = self._client.klines(item.symbol, "15m", limit=35)
        if len(klines_15m) < 25:
            return None

        pl = item.platform_low

        post = [k for k in klines_15m if k.close_time_ms > item.triggered_at_ms]
        if len(post) < 2:
            return None

        # 必须有反抽到 PlatformLow ±1% 的 K 线
        retest_zone_lo = pl * (1 - _RETEST_TOL)
        retest_zone_hi = pl * (1 + _RETEST_TOL)
        has_retest = any(
            k.low <= retest_zone_hi and k.high >= retest_zone_lo
            for k in post[:-1]
        )
        if not has_retest:
            return None

        # 反抽阶段至少一根 15M 量低于均量
        vma = vol_ma(klines_15m, period=20)
        has_low_vol = any(k.volume < vma for k in post[:-1])
        if not has_low_vol:
            return None

        # 触发 K 线条件
        cur  = post[-1]
        prev = post[-2]
        if cur.close >= pl:
            return None   # 须重新收盘在平台下方
        if cur.close >= cur.open:
            return None   # 须为阴线
        if cur.close >= prev.low:
            return None   # 须跌破前一根最低价
        cur_vr = vol_ratio(klines_15m, period=20)
        if cur_vr < _15M_VOL_THRESH:
            return None

        # 止损：反抽期间最高的 swing high 上方 0.2%
        sl_raw = swing_high(post[:-1], lookback=len(post) - 1) * Decimal("1.002")
        entry  = cur.close
        sl     = clamp_stop(entry, sl_raw, "SHORT")
        if sl is None:
            return None

        risk = sl - entry
        tp1  = entry - risk
        tp2  = entry - risk * Decimal("2")
        rr   = Decimal("2.00")

        stop_pct = (sl - entry) / entry * 100

        return CandidateInput(
            strategy_id=_STRATEGY_ID,
            symbol=item.symbol,
            direction="SHORT",
            entry=str(entry),
            sl=str(sl),
            tp1=str(tp1),
            tp2=str(tp2),
            rr=str(rr),
            confidence=0.65,
            stop_pct=float(stop_pct.quantize(Decimal("0.01"))),
            change_24h=0.0,
            quote_volume=0.0,
            volume_ratio=float(cur_vr),
            atr=float(risk),
            ema20=float(pl),
            ema60=None,
            market_regime="放量跌破反抽",
            reason=(
                f"1H 放量跌破平台 (PL={float(pl):.4f}, vr={float(item.breakout_vol_ratio):.2f}x)；"
                f"15M 缩量反抽后阴线收盘跌回 PL 下方；"
                f"量比 {float(cur_vr):.2f}x；止损距离 {float(stop_pct):.1f}%。"
            ),
        )

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({"strategy_version": "wave_short", "source": "wave_breakdown_retest"})
        return base
