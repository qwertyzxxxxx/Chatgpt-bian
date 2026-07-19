"""RSD Short — RSI Divergence BOS Short (影子策略 v1).

邏輯:
  D1  不得處於剛剛放量突破並連續創新高的強多頭狀態
  H4  已跌破最近Higher Low 或 已形成Lower High（下降結構）
  H1  最近8根發生向下BOS（幅度≥0.15ATR，量比≥1.2x）→ 進入觀察窗
  反弹 30-60%反彈 + 價格進入BOS位±0.30 M15 ATR
  M15 頂背離（price HH + RSI LH，兩RSI均>50，至少一個≥60）
  觸發 M15收盤跌破兩高點之間內部擺動低點（不等跌破整個下降推動最低點）
  止損 第二背離高點 + 0.15 ATR；SL<0.60ATR 或 >5% 拒絕
  止盈 固定2R；下方最近H1支撐至少2.20R空間

Strategy ID: rsd_short
Signal prefix: RSD
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.v3.candidates.repository import CandidateInput
from binance_ai_trader.v3.strategies.base import V3Strategy
from binance_ai_trader.v3.strategies.rsd_common import (
    BOS_WATCH_HOURS,
    ENTRY_MAX_SLIPPAGE_ATR,
    MAX_STOP_PCT,
    MIN_AVAILABLE_R,
    MIN_QUOTE_VOLUME_24H,
    MIN_STOP_ATR,
    RSD_SHORT_ID,
    STOP_BUFFER_ATR,
    TARGET_RR,
    TOP_N_UNIVERSE,
    atr14,
    d1_not_strong_bull,
    find_bearish_divergence,
    find_h1_downward_bos,
    h4_downtrend_structure,
    nearest_h1_support,
    rsi_series,
    vol_ma20,
)
from binance_ai_trader.v3.strategies.wave_common import _LEVERAGE, _STABLE
from binance_ai_trader.v3.strategies.wave_watchlist_repo import WaveWatchlistRepo

log = logging.getLogger(__name__)

_STRATEGY_ID = RSD_SHORT_ID
_HOLD_HOURS  = 48


class RSDivShortStrategy(V3Strategy):
    """RSI Divergence BOS Short — 影子策略。

    兩階段:
      Phase-1: Top-100 中找 H1 向下 BOS → 加入觀察名單
      Phase-2: 觀察名單中找 M15 頂背離 + 觸發信號
    """

    def __init__(
        self,
        client: BinancePublicClient,
        db_path: Path,
    ) -> None:
        self._client = client
        self._repo   = WaveWatchlistRepo(db_path)

    @property
    def strategy_id(self) -> str:
        return _STRATEGY_ID

    # ── Main entry ────────────────────────────────────────────────────────────

    def generate_candidates(self, now: datetime | None = None) -> list[CandidateInput]:
        now = self._now(now)
        self._repo.expire_old(now)

        candidates: list[CandidateInput] = []
        for item in self._repo.get_active(_STRATEGY_ID):
            try:
                if self._should_invalidate(item):
                    self._repo.invalidate(item.watch_id)
                    log.debug("[rsd_short] invalidate %s (%s)", item.symbol, item.watch_id)
                    continue
                cand = self._check_m15_entry(item)
                if cand:
                    self._repo.mark_entered(item.watch_id)
                    candidates.append(cand)
            except Exception:
                log.exception("[rsd_short] Phase-2 error: %s", item.symbol)

        try:
            universe = self._get_universe()
        except Exception:
            log.exception("[rsd_short] universe fetch failed")
            return candidates

        watching = {it.symbol for it in self._repo.get_active(_STRATEGY_ID)}
        for sym, qv in universe:
            if sym in watching:
                continue
            try:
                self._scan_for_bos(sym, qv, now)
            except Exception:
                log.debug("[rsd_short] scan_for_bos error: %s", sym)

        log.info("[rsd_short] %d candidates", len(candidates))
        return candidates

    # ── Universe helper ───────────────────────────────────────────────────────

    def _get_universe(self) -> list[tuple[str, Decimal]]:
        tickers = self._client.tickers_24h()
        keep: list[tuple[str, Decimal]] = []
        for t in tickers:
            sym = t.symbol
            if not sym.endswith("USDT"):
                continue
            base = sym[:-4]
            if any(kw in base for kw in _STABLE):
                continue
            if any(base.endswith(kw) or base.startswith(kw) for kw in _LEVERAGE):
                continue
            if t.quote_volume < MIN_QUOTE_VOLUME_24H:
                continue
            keep.append((sym, t.quote_volume))
        keep.sort(key=lambda x: x[1], reverse=True)
        return keep[:TOP_N_UNIVERSE]

    # ── Phase-1: BOS scan ─────────────────────────────────────────────────────

    def _scan_for_bos(self, symbol: str, quote_volume: Decimal, now: datetime) -> None:
        # D1: reject strong bull state
        d1 = self._client.klines(symbol, "1d", limit=65)
        if len(d1) < 10:
            return
        if not d1_not_strong_bull(d1):
            log.debug("[rsd_short] %s D1 strong bull — skip", symbol)
            return

        # H4: requires bearish structure
        h4 = self._client.klines(symbol, "4h", limit=50)
        if len(h4) < 20:
            return
        if not h4_downtrend_structure(h4):
            log.debug("[rsd_short] %s H4 no bearish structure", symbol)
            return

        # H1 downward BOS
        h1 = self._client.klines(symbol, "1h", limit=40)
        if len(h1) < 20:
            return
        bos = find_h1_downward_bos(h1)
        if bos is None:
            return

        bos_time_iso = datetime.fromtimestamp(
            bos.bos_time_ms / 1000, tz=UTC
        ).isoformat(timespec="seconds")

        # SHORT watchlist mapping:
        #   platform_high  = impulse_high (peak before breakdown)
        #   platform_low   = bos_level    (support broken, now resistance)
        #   breakout_close = impulse_low  (close of BOS-down bar)
        self._repo.add(
            symbol=symbol,
            strategy_id=_STRATEGY_ID,
            direction="SHORT",
            platform_high=bos.impulse_high,
            platform_low=bos.bos_level,
            breakout_close=bos.impulse_low,
            breakout_vol_ratio=bos.bos_vol_ratio,
            triggered_at=bos_time_iso,
            triggered_at_ms=bos.bos_time_ms,
            watch_hours=BOS_WATCH_HOURS,
        )
        log.info(
            "[rsd_short] BOS detected %s  bos_level=%.6f impulse_high=%.6f vr=%.2fx",
            symbol, float(bos.bos_level), float(bos.impulse_high), float(bos.bos_vol_ratio),
        )

    # ── Invalidation check ────────────────────────────────────────────────────

    def _should_invalidate(self, item) -> bool:
        """Invalidate if H1 close rises above bos_level (recovery — SHORT setup failed)."""
        try:
            h1 = self._client.klines(item.symbol, "1h", limit=5)
        except Exception:
            return False
        closed = h1[:-1]
        if not closed:
            return False
        bos_level = item.platform_low  # SHORT: bos_level stored as platform_low
        return closed[-1].close > bos_level * Decimal("1.005")

    # ── Phase-2: M15 entry check ──────────────────────────────────────────────

    def _check_m15_entry(self, item) -> CandidateInput | None:
        m15_raw = self._client.klines(item.symbol, "15m", limit=55)
        h1      = self._client.klines(item.symbol, "1h",  limit=35)
        if len(m15_raw) < 30:
            return None

        m15_closed = m15_raw[:-1]
        atr15  = atr14(m15_closed)
        vma15  = vol_ma20(m15_closed)
        if atr15 == 0:
            return None

        rsi_vals = rsi_series(m15_closed)

        # SHORT watchlist field mapping:
        impulse_high = item.platform_high   # peak before breakdown
        bos_level    = item.platform_low    # support broken, now resistance
        impulse_low  = item.breakout_close  # BOS-down bar close

        div = find_bearish_divergence(
            klines=m15_closed,
            rsi_vals=rsi_vals,
            atr_val=atr15,
            vma=vma15,
            bos_time_ms=item.triggered_at_ms,
            impulse_high=impulse_high,
            impulse_low=impulse_low,
            bos_level=bos_level,
        )
        if div is None:
            return None

        trigger_close = div.trigger_close
        current_open  = m15_raw[-1].open

        # Cancel if current bar already moved > 0.20 ATR below trigger (SHORT)
        if current_open < trigger_close - ENTRY_MAX_SLIPPAGE_ATR * atr15:
            log.info(
                "[rsd_short] %s slippage cancel — open=%.6f < trigger=%.6f - %.2f ATR",
                item.symbol, float(current_open), float(trigger_close), float(ENTRY_MAX_SLIPPAGE_ATR),
            )
            self._repo.invalidate(item.watch_id)
            return None

        # Stop loss: second divergence high + STOP_BUFFER_ATR * ATR
        raw_sl   = div.pivot2_price + STOP_BUFFER_ATR * atr15
        entry    = trigger_close
        risk     = raw_sl - entry
        if risk <= 0:
            return None

        if risk < MIN_STOP_ATR * atr15:
            log.debug("[rsd_short] %s SL too tight (%.4f < %.4f ATR)", item.symbol, float(risk), float(MIN_STOP_ATR * atr15))
            return None
        stop_pct = risk / entry * Decimal("100")
        if stop_pct > MAX_STOP_PCT:
            log.debug("[rsd_short] %s stop_pct=%.2f%% > %.1f%%", item.symbol, float(stop_pct), float(MAX_STOP_PCT))
            return None

        tp1 = entry - risk
        tp2 = entry - risk * TARGET_RR

        # Available R: nearest H1 support below entry must provide ≥ MIN_AVAILABLE_R
        support = nearest_h1_support(h1, below=entry)
        if support is None:
            available_r = TARGET_RR
        else:
            available_r = (entry - support) / risk
        if available_r < MIN_AVAILABLE_R:
            log.debug(
                "[rsd_short] %s available_r=%.2f < %.2f",
                item.symbol, float(available_r), float(MIN_AVAILABLE_R),
            )
            return None

        meta = json.dumps({
            "strategy":          "rsd_short",
            "bos_level":         str(bos_level),
            "impulse_high":      str(impulse_high),
            "impulse_low":       str(impulse_low),
            "bos_vol_ratio":     str(item.breakout_vol_ratio),
            "h1_bos_time":       item.triggered_at,
            "rebound_ratio":     str(((div.pivot2_price - impulse_low) / (impulse_high - impulse_low)).quantize(Decimal("0.001"))),
            "div_price_1":       str(div.pivot1_price),
            "div_rsi_1":         str(div.pivot1_rsi.quantize(Decimal("0.1"))),
            "div_price_2":       str(div.pivot2_price),
            "div_rsi_2":         str(div.pivot2_rsi.quantize(Decimal("0.1"))),
            "internal_sl":       str(div.internal_key),
            "vol_pullback_ratio":str(div.vol_pullback.quantize(Decimal("0.01"))),
            "vol_confirm_ratio": str(div.vol_confirm.quantize(Decimal("0.01"))),
            "available_r":       str(available_r.quantize(Decimal("0.01"))),
            "h1_support":        str(support) if support else "none",
        })

        rebound_ratio = ((div.pivot2_price - impulse_low) / (impulse_high - impulse_low)).quantize(Decimal("0.01"))
        reason = (
            f"RSD頂背離: price {float(div.pivot1_price):.4f}↑{float(div.pivot2_price):.4f} "
            f"RSI {float(div.pivot1_rsi):.1f}↓{float(div.pivot2_rsi):.1f} | "
            f"BOS={float(bos_level):.4f} 反彈{float(rebound_ratio):.0%} "
            f"觸發跌破內部低={float(div.internal_key):.4f} | "
            f"可用R={float(available_r):.2f} vol確認={float(div.vol_confirm):.2f}x"
        )

        return CandidateInput(
            strategy_id=_STRATEGY_ID,
            symbol=item.symbol,
            direction="SHORT",
            entry=str(entry),
            sl=str(raw_sl),
            tp1=str(tp1),
            tp2=str(tp2),
            rr=str(TARGET_RR),
            confidence=min(1.0, float(available_r) / float(MIN_AVAILABLE_R) * 0.8),
            stop_pct=float(stop_pct.quantize(Decimal("0.01"))),
            change_24h=0.0,
            quote_volume=0.0,
            volume_ratio=float(div.vol_confirm.quantize(Decimal("0.01"))),
            atr=float(atr15),
            ema20=float(bos_level),
            ema60=float(impulse_high),
            market_regime=f"RSD_SHORT|BOS_DOWN|rb={float(rebound_ratio):.0%}",
            reason=reason,
            meta_json=meta,
        )

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({"strategy_version": "rsd_short_v1", "source": "rsi_divergence_bos_short"})
        return base
