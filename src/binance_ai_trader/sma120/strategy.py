"""SMA120 V1.9-D — signal detection for XAUUSDT.

Entry: M15 trend filter + M5 pullback-to-EMA20 breakout + H1 filter (long only).
Parameters are frozen; never optimise SL/TP/ATR range without a full review.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.sma120.config import (
    ATR_MAX,
    ATR_MIN,
    ATR_PERIOD,
    EMA_FAST,
    EMA_SLOW,
    H1_INTERVAL,
    H1_LIMIT,
    M15_INTERVAL,
    M15_LIMIT,
    M5_INTERVAL,
    M5_LIMIT,
    MAX_EXTENSION_ATR,
    SL_DISTANCE,
    SMA_PERIOD,
    SYMBOL,
    TP_DISTANCE,
)
from binance_ai_trader.sma120.indicators import atr_last, ema_last_two, ema_series, sma_last

log = logging.getLogger(__name__)


@dataclass
class SMA120Signal:
    direction: str          # "LONG" or "SHORT"
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    rr: Decimal             # always 2.0
    signal_candle_time_ms: int
    m5_atr: Decimal
    m5_ema20: Decimal
    m5_sma120: Decimal


class SMA120Strategy:
    def __init__(self, client: BinancePublicClient) -> None:
        self._client = client

    def scan(self) -> SMA120Signal | None:
        """Return a signal if all entry conditions are met, else None."""
        try:
            return self._scan()
        except Exception:
            log.exception("[SMA120] scan error")
            return None

    # ──────────────────────────────────────────────────────────────────────
    def _scan(self) -> SMA120Signal | None:
        m5_raw  = self._client.klines(SYMBOL, M5_INTERVAL,  limit=M5_LIMIT)
        m15_raw = self._client.klines(SYMBOL, M15_INTERVAL, limit=M15_LIMIT)
        h1_raw  = self._client.klines(SYMBOL, H1_INTERVAL,  limit=H1_LIMIT)

        # Drop the currently-forming (not-yet-closed) candle from each TF
        m5  = m5_raw[:-1]
        m15 = m15_raw[:-1]
        h1  = h1_raw[:-1]

        if len(m5) < SMA_PERIOD + ATR_PERIOD + 5:
            log.debug("[SMA120] not enough M5 candles: %d", len(m5))
            return None
        if len(m15) < EMA_SLOW + 5:
            log.debug("[SMA120] not enough M15 candles: %d", len(m15))
            return None
        if len(h1) < EMA_SLOW + 5:
            log.debug("[SMA120] not enough H1 candles: %d", len(h1))
            return None

        signal_candle = m5[-1]

        # ── M5 indicators ─────────────────────────────────────────────────
        m5_c = [Decimal(str(k.close)) for k in m5]
        m5_h = [Decimal(str(k.high))  for k in m5]
        m5_l = [Decimal(str(k.low))   for k in m5]

        m5_sma120 = sma_last(m5_c, SMA_PERIOD)
        m5_ema20  = ema_last_two(m5_c, EMA_FAST)[0]
        m5_atr    = atr_last(m5_h, m5_l, m5_c, ATR_PERIOD)

        # ── ATR gate — must check before anything else ─────────────────
        if not (ATR_MIN <= m5_atr <= ATR_MAX):
            log.debug("[SMA120] ATR out of range: %.2f (need %.2f–%.2f)", m5_atr, ATR_MIN, ATR_MAX)
            return None

        # ── Direction from SMA120 ──────────────────────────────────────
        sig_close = Decimal(str(signal_candle.close))
        if sig_close > m5_sma120:
            direction  = "LONG"
            extension  = sig_close - m5_sma120
        elif sig_close < m5_sma120:
            direction  = "SHORT"
            extension  = m5_sma120 - sig_close
        else:
            return None

        # Extension must be positive and ≤ 3×ATR
        if extension <= 0 or extension > MAX_EXTENSION_ATR * m5_atr:
            log.debug("[SMA120] extension %.2f outside (0, %.2f]", extension, MAX_EXTENSION_ATR * m5_atr)
            return None

        # ── M5 pullback-to-EMA20 + breakout ────────────────────────────
        if not _check_pullback_breakout(m5, m5_ema20, direction):
            log.debug("[SMA120] pullback/breakout check failed for %s", direction)
            return None

        # ── M15 trend filter ───────────────────────────────────────────
        m15_c = [Decimal(str(k.close)) for k in m15]
        m15_ema20_cur, m15_ema20_prv = ema_last_two(m15_c, EMA_FAST)
        m15_ema60                    = ema_last_two(m15_c, EMA_SLOW)[0]
        m15_close                    = Decimal(str(m15[-1].close))

        if direction == "LONG":
            if m15_ema20_cur <= m15_ema60:
                log.debug("[SMA120] M15: EMA20 not above EMA60")
                return None
            if m15_ema20_cur <= m15_ema20_prv:
                log.debug("[SMA120] M15: EMA20 slope not up")
                return None
            if m15_close <= m15_ema20_cur:
                log.debug("[SMA120] M15: close not above EMA20")
                return None
        else:  # SHORT
            if m15_ema20_cur >= m15_ema60:
                log.debug("[SMA120] M15: EMA20 not below EMA60")
                return None
            if m15_ema20_cur >= m15_ema20_prv:
                log.debug("[SMA120] M15: EMA20 slope not down")
                return None
            if m15_close >= m15_ema20_cur:
                log.debug("[SMA120] M15: close not below EMA20")
                return None

        # ── H1 filter (LONG only) ──────────────────────────────────────
        if direction == "LONG":
            signal_time_s = signal_candle.open_time_ms // 1000
            # Only accept H1 candles whose 1-hour window has fully elapsed
            h1_valid = [k for k in h1 if (k.open_time_ms // 1000 + 3600) <= signal_time_s]
            if len(h1_valid) < EMA_SLOW + 3:
                log.debug("[SMA120] not enough valid H1 candles: %d", len(h1_valid))
                return None

            h1_c = [Decimal(str(k.close)) for k in h1_valid]
            h1_ema20_cur, h1_ema20_prv = ema_last_two(h1_c, EMA_FAST)
            h1_ema60                   = ema_last_two(h1_c, EMA_SLOW)[0]
            h1_close                   = Decimal(str(h1_valid[-1].close))

            if h1_ema20_cur <= h1_ema60:
                log.debug("[SMA120] H1: EMA20 not above EMA60")
                return None
            if h1_close <= h1_ema20_cur:
                log.debug("[SMA120] H1: close not above EMA20")
                return None
            if h1_ema20_cur <= h1_ema20_prv:
                log.debug("[SMA120] H1: EMA20 slope not up")
                return None

        # ── Build signal ───────────────────────────────────────────────
        entry = sig_close
        if direction == "LONG":
            sl  = entry - SL_DISTANCE
            tp1 = entry + TP_DISTANCE
        else:
            sl  = entry + SL_DISTANCE
            tp1 = entry - TP_DISTANCE

        log.info(
            "[SMA120] %s signal: entry=%.2f SL=%.2f TP=%.2f ATR=%.2f ext=%.2f",
            direction, entry, sl, tp1, m5_atr, extension,
        )

        return SMA120Signal(
            direction=direction,
            entry=entry,
            stop_loss=sl,
            tp1=tp1,
            rr=TP_DISTANCE / SL_DISTANCE,
            signal_candle_time_ms=signal_candle.open_time_ms,
            m5_atr=m5_atr,
            m5_ema20=m5_ema20,
            m5_sma120=m5_sma120,
        )


# ── Helper ─────────────────────────────────────────────────────────────────

def _check_pullback_breakout(m5_closed: list, m5_ema20: Decimal, direction: str) -> bool:
    """Verify that among the 5 candles immediately before the signal candle at
    least one entered the EMA20 zone (pullback), AND the signal candle closes
    on the correct side of EMA20 (breakout).

    Pullback zone tolerance: within 0.1% of EMA20.
    """
    signal  = m5_closed[-1]
    lookback = m5_closed[-6:-1]
    if len(lookback) < 3:
        return False

    if direction == "LONG":
        pullback_ceil = m5_ema20 * Decimal("1.001")
        pullback = any(Decimal(str(k.low)) <= pullback_ceil for k in lookback)
        breakout = Decimal(str(signal.close)) > m5_ema20
    else:  # SHORT
        pullback_floor = m5_ema20 * Decimal("0.999")
        pullback = any(Decimal(str(k.high)) >= pullback_floor for k in lookback)
        breakout = Decimal(str(signal.close)) < m5_ema20

    return pullback and breakout
