"""RSD Long — RSI Divergence Pullback Long (影子策略 v1).

邏輯:
  D1  最近重要Swing Low未被收盤跌破
  H4  Higher High + Higher Low（上升結構有效）
  H1  最近12根發生向上BOS（幅度≥0.15ATR，量比≥1.2x）→ 進入觀察窗
  回踩 30-60%回撤 + 價格進入BOS位±0.30 M15 ATR
  M15 底背離（price LL + RSI HL，兩RSI均<50，至少一個≤40）
  觸發 M15收盤突破兩低點之間內部擺動高點
  止損 第二背離低點 - 0.15 ATR；SL<0.60ATR 或 >5% 拒絕
  止盈 固定2R；上方最近H1壓力至少2.20R空間

Strategy ID: rsd_long
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
    RSD_LONG_ID,
    STOP_BUFFER_ATR,
    TARGET_RR,
    TOP_N_UNIVERSE,
    atr14,
    d1_swing_low_intact,
    find_bullish_divergence,
    find_h1_upward_bos,
    h4_uptrend_structure,
    nearest_h1_resistance,
    rsi_series,
    vol_ma20,
)
from binance_ai_trader.v3.strategies.wave_common import _LEVERAGE, _STABLE
from binance_ai_trader.v3.strategies.wave_watchlist_repo import WaveWatchlistRepo

log = logging.getLogger(__name__)

_STRATEGY_ID = RSD_LONG_ID
_HOLD_HOURS  = 48


class RSDivLongStrategy(V3Strategy):
    """RSI Divergence Pullback Long — 影子策略。

    兩階段:
      Phase-1 (每次掃描): 從 Top-100 USDT 合約中找 H1 向上 BOS，加入觀察名單
      Phase-2 (每次掃描): 對觀察名單中的品種在 M15 找底背離 + 觸發信號
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

        # Phase 2: check existing watchlist items for M15 entry
        candidates: list[CandidateInput] = []
        for item in self._repo.get_active(_STRATEGY_ID):
            try:
                if self._should_invalidate(item):
                    self._repo.invalidate(item.watch_id)
                    log.debug("[rsd_long] invalidate %s (%s)", item.symbol, item.watch_id)
                    continue
                cand = self._check_m15_entry(item)
                if cand:
                    self._repo.mark_entered(item.watch_id)
                    candidates.append(cand)
            except Exception:
                log.exception("[rsd_long] Phase-2 error: %s", item.symbol)

        # Phase 1: scan universe for new H1 BOS
        try:
            universe = self._get_universe()
        except Exception:
            log.exception("[rsd_long] universe fetch failed")
            return candidates

        watching = {it.symbol for it in self._repo.get_active(_STRATEGY_ID)}
        for sym, qv in universe:
            if sym in watching:
                continue
            try:
                self._scan_for_bos(sym, qv, now)
            except Exception:
                log.debug("[rsd_long] scan_for_bos error: %s", sym)

        log.info("[rsd_long] %d candidates", len(candidates))
        return candidates

    # ── Universe helper ───────────────────────────────────────────────────────

    def _get_universe(self) -> list[tuple[str, Decimal]]:
        """Top-100 USDT perpetuals by 24h quote volume, filtered by MIN_QUOTE_VOLUME_24H."""
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
        # D1 structure
        d1 = self._client.klines(symbol, "1d", limit=65)
        if len(d1) < 10:
            return
        intact, d1_sl = d1_swing_low_intact(d1)
        if not intact:
            log.debug("[rsd_long] %s D1 swing low broken", symbol)
            return

        # H4 structure
        h4 = self._client.klines(symbol, "4h", limit=50)
        if len(h4) < 20:
            return
        is_uptrend, recent_hl = h4_uptrend_structure(h4)
        if not is_uptrend:
            log.debug("[rsd_long] %s H4 no uptrend structure", symbol)
            return

        # H1 BOS
        h1 = self._client.klines(symbol, "1h", limit=40)
        if len(h1) < 20:
            return
        bos = find_h1_upward_bos(h1)
        if bos is None:
            return

        bos_time_iso = datetime.fromtimestamp(
            bos.bos_time_ms / 1000, tz=UTC
        ).isoformat(timespec="seconds")

        self._repo.add(
            symbol=symbol,
            strategy_id=_STRATEGY_ID,
            direction="LONG",
            platform_high=bos.bos_level,
            platform_low=bos.impulse_low,
            breakout_close=bos.impulse_high,
            breakout_vol_ratio=bos.bos_vol_ratio,
            triggered_at=bos_time_iso,
            triggered_at_ms=bos.bos_time_ms,
            watch_hours=BOS_WATCH_HOURS,
        )
        log.info(
            "[rsd_long] BOS detected %s  bos_level=%.6f impulse_low=%.6f vr=%.2fx",
            symbol, float(bos.bos_level), float(bos.impulse_low), float(bos.bos_vol_ratio),
        )

    # ── Invalidation check ────────────────────────────────────────────────────

    def _should_invalidate(self, item) -> bool:
        """Invalidate if H1 close falls below bos_level (BOS structure failed)."""
        try:
            h1 = self._client.klines(item.symbol, "1h", limit=5)
        except Exception:
            return False
        closed = h1[:-1]
        if not closed:
            return False
        return closed[-1].close < item.platform_high * Decimal("0.995")

    # ── Phase-2: M15 entry check ──────────────────────────────────────────────

    def _check_m15_entry(self, item) -> CandidateInput | None:
        # Fetch klines (raw includes current open bar at [-1])
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

        bos_level    = item.platform_high    # resistance level broken
        impulse_low  = item.platform_low
        impulse_high = item.breakout_close

        div = find_bullish_divergence(
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

        # Cancel if current bar already moved > 0.20 ATR beyond trigger
        if current_open > trigger_close + ENTRY_MAX_SLIPPAGE_ATR * atr15:
            log.info(
                "[rsd_long] %s slippage cancel — open=%.6f > trigger=%.6f + %.2f ATR",
                item.symbol, float(current_open), float(trigger_close), float(ENTRY_MAX_SLIPPAGE_ATR),
            )
            self._repo.invalidate(item.watch_id)
            return None

        # Stop loss: second divergence low - STOP_BUFFER_ATR * ATR
        raw_sl   = div.pivot2_price - STOP_BUFFER_ATR * atr15
        entry    = trigger_close
        risk     = entry - raw_sl
        if risk <= 0:
            return None

        # Validate stop
        if risk < MIN_STOP_ATR * atr15:
            log.debug("[rsd_long] %s SL too tight (%.4f < %.4f ATR)", item.symbol, float(risk), float(MIN_STOP_ATR * atr15))
            return None
        stop_pct = risk / entry * Decimal("100")
        if stop_pct > MAX_STOP_PCT:
            log.debug("[rsd_long] %s stop_pct=%.2f%% > %.1f%%", item.symbol, float(stop_pct), float(MAX_STOP_PCT))
            return None

        tp1 = entry + risk
        tp2 = entry + risk * TARGET_RR

        # Available R: nearest H1 resistance must provide ≥ MIN_AVAILABLE_R
        resistance = nearest_h1_resistance(h1, above=entry)
        if resistance is None:
            available_r = TARGET_RR  # assume OK if no resistance found
        else:
            available_r = (resistance - entry) / risk
        if available_r < MIN_AVAILABLE_R:
            log.debug(
                "[rsd_long] %s available_r=%.2f < %.2f",
                item.symbol, float(available_r), float(MIN_AVAILABLE_R),
            )
            return None

        # Build metadata
        meta = json.dumps({
            "strategy":          "rsd_long",
            "bos_level":         str(bos_level),
            "impulse_high":      str(impulse_high),
            "impulse_low":       str(impulse_low),
            "bos_vol_ratio":     str(item.breakout_vol_ratio),
            "h1_bos_time":       item.triggered_at,
            "pullback_ratio":    str(((impulse_high - div.pivot2_price) / (impulse_high - impulse_low)).quantize(Decimal("0.001"))),
            "div_price_1":       str(div.pivot1_price),
            "div_rsi_1":         str(div.pivot1_rsi.quantize(Decimal("0.1"))),
            "div_price_2":       str(div.pivot2_price),
            "div_rsi_2":         str(div.pivot2_rsi.quantize(Decimal("0.1"))),
            "internal_sh":       str(div.internal_key),
            "vol_pullback_ratio":str(div.vol_pullback.quantize(Decimal("0.01"))),
            "vol_confirm_ratio": str(div.vol_confirm.quantize(Decimal("0.01"))),
            "available_r":       str(available_r.quantize(Decimal("0.01"))),
            "h1_resistance":     str(resistance) if resistance else "none",
        })

        pullback_ratio = ((impulse_high - div.pivot2_price) / (impulse_high - impulse_low)).quantize(Decimal("0.01"))
        reason = (
            f"RSD底背離: price {float(div.pivot1_price):.4f}↓{float(div.pivot2_price):.4f} "
            f"RSI {float(div.pivot1_rsi):.1f}↑{float(div.pivot2_rsi):.1f} | "
            f"BOS={float(bos_level):.4f} 回撤{float(pullback_ratio):.0%} "
            f"觸發突破內部高={float(div.internal_key):.4f} | "
            f"可用R={float(available_r):.2f} vol確認={float(div.vol_confirm):.2f}x"
        )

        return CandidateInput(
            strategy_id=_STRATEGY_ID,
            symbol=item.symbol,
            direction="LONG",
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
            ema60=float(impulse_low),
            market_regime=f"RSD_LONG|BOS_UP|pb={float(pullback_ratio):.0%}",
            reason=reason,
            meta_json=meta,
        )

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({"strategy_version": "rsd_long_v1", "source": "rsi_divergence_pullback"})
        return base
