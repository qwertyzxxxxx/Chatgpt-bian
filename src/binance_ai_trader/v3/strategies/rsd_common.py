"""RSI Divergence strategies — shared constants, indicators, and pattern detection.

Strategy IDs:
  rsd_long   RSI Divergence Pullback Long  (H1 向上BOS → 回踩 → M15 底背离)
  rsd_short  RSI Divergence BOS Short      (H1 向下BOS → 反弹 → M15 顶背离)

Signal prefix: RSD
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from binance_ai_trader.infrastructure.binance_public import Kline
from binance_ai_trader.v3.strategies.reversal_indicators import (
    atr as _atr_helper,
    rsi as _rsi_helper,
)

# ── Strategy IDs ──────────────────────────────────────────────────────────────
RSD_LONG_ID  = "rsd_long"
RSD_SHORT_ID = "rsd_short"

# ── Shared parameters (spec-defined) ─────────────────────────────────────────
RSI_PERIOD              = 14
PIVOT_LEFT              = 2
PIVOT_RIGHT             = 2
DIVERGENCE_MIN_BARS     = 4
DIVERGENCE_MAX_BARS     = 24
RSI_MIN_DIFFERENCE      = Decimal("4")
PRICE_MIN_DIFF_ATR      = Decimal("0.05")
MIN_PULLBACK_RATIO      = Decimal("0.30")
MAX_PULLBACK_RATIO      = Decimal("0.60")
IMPULSE_VOL_RATIO       = Decimal("1.20")
PULLBACK_VOL_RATIO_MAX  = Decimal("0.90")
CONFIRM_VOL_RATIO       = Decimal("1.20")
ENTRY_MAX_SLIPPAGE_ATR  = Decimal("0.20")
STOP_BUFFER_ATR         = Decimal("0.15")
MIN_STOP_ATR            = Decimal("0.60")
MAX_STOP_PCT            = Decimal("5.0")
TARGET_RR               = Decimal("2.0")
MIN_AVAILABLE_R         = Decimal("2.20")
MIN_QUOTE_VOLUME_24H    = Decimal("20000000")
BOS_WATCH_HOURS         = 6
H1_BOS_LOOKBACK_LONG    = 12
H1_BOS_LOOKBACK_SHORT   = 8
H1_BOS_MIN_AMP_ATR      = Decimal("0.15")
PULLBACK_ZONE_TOL_ATR   = Decimal("0.30")
TOP_N_UNIVERSE          = 100


# ── Data transfer objects ─────────────────────────────────────────────────────

@dataclass
class BosState:
    """Result of H1 BOS detection."""
    impulse_low:   Decimal
    bos_level:     Decimal
    impulse_high:  Decimal
    bos_time_ms:   int
    bos_vol_ratio: Decimal


@dataclass
class DivergenceResult:
    """M15 RSI divergence + trigger state."""
    pivot1_idx:    int
    pivot1_price:  Decimal
    pivot1_rsi:    Decimal
    pivot2_idx:    int
    pivot2_price:  Decimal
    pivot2_rsi:    Decimal
    internal_key:  Decimal
    trigger_close: Decimal
    trigger_idx:   int
    vol_pullback:  Decimal
    vol_confirm:   Decimal


# ── Indicator helpers ─────────────────────────────────────────────────────────

def atr14(klines: Sequence[Kline]) -> Decimal:
    highs  = [k.high  for k in klines]
    lows   = [k.low   for k in klines]
    closes = [k.close for k in klines]
    result = _atr_helper(highs, lows, closes, period=14)
    return result if result is not None else Decimal("0")


def vol_ma20(klines: Sequence[Kline]) -> Decimal:
    """20-bar volume MA excluding the last bar."""
    if len(klines) < 21:
        return Decimal("0")
    window = [k.volume for k in klines[-21:-1]]
    return sum(window) / Decimal(20)


def rsi_series(klines: Sequence[Kline]) -> list[Decimal | None]:
    """RSI(14) for every bar (None for bars before period+1)."""
    closes = [k.close for k in klines]
    out: list[Decimal | None] = []
    for i in range(len(closes)):
        if i < RSI_PERIOD:
            out.append(None)
        else:
            out.append(_rsi_helper(closes[:i + 1], period=RSI_PERIOD))
    return out


# ── Pivot detection ───────────────────────────────────────────────────────────

def find_pivot_lows(klines: Sequence[Kline]) -> list[int]:
    """Indices of confirmed pivot lows (PIVOT_LEFT=2, PIVOT_RIGHT=2 neighbours)."""
    n, result = len(klines), []
    for i in range(PIVOT_LEFT, n - PIVOT_RIGHT):
        p = klines[i].low
        if all(p < klines[i - j].low for j in range(1, PIVOT_LEFT + 1)) and \
           all(p < klines[i + j].low for j in range(1, PIVOT_RIGHT + 1)):
            result.append(i)
    return result


def find_pivot_highs(klines: Sequence[Kline]) -> list[int]:
    """Indices of confirmed pivot highs."""
    n, result = len(klines), []
    for i in range(PIVOT_LEFT, n - PIVOT_RIGHT):
        p = klines[i].high
        if all(p > klines[i - j].high for j in range(1, PIVOT_LEFT + 1)) and \
           all(p > klines[i + j].high for j in range(1, PIVOT_RIGHT + 1)):
            result.append(i)
    return result


# ── D1 structure ──────────────────────────────────────────────────────────────

def d1_swing_low_intact(d1: Sequence[Kline]) -> tuple[bool, Decimal]:
    """Return (intact, swing_low_price). intact=False if D1 close broke below swing low."""
    closed = d1[:-1]
    if len(closed) < 10:
        return False, Decimal("0")
    pivots = find_pivot_lows(closed[-60:] if len(closed) > 60 else closed)
    if not pivots:
        return True, Decimal("0")
    window = closed[-60:] if len(closed) > 60 else closed
    swing_low = min(window[i].low for i in pivots)
    return closed[-1].close > swing_low, swing_low


def d1_not_strong_bull(d1: Sequence[Kline]) -> bool:
    """True = NOT in strong bull breakout (SHORT is allowed).
    Rejects SHORT when: last 5 D1 all green AND close > 20-bar prior high.
    """
    closed = d1[:-1]
    if len(closed) < 25:
        return True
    last5 = closed[-5:]
    if not all(k.close > k.open for k in last5):
        return True
    prior20_high = max(k.high for k in closed[-21:-1])
    return closed[-1].close <= prior20_high


# ── H4 structure ──────────────────────────────────────────────────────────────

def h4_uptrend_structure(h4: Sequence[Kline]) -> tuple[bool, Decimal]:
    """(is_uptrend, recent_higher_low). Requires ≥1 HH and ≥1 HL in last pivots."""
    closed = h4[:-1]
    if len(closed) < 20:
        return False, Decimal("0")
    low_p  = find_pivot_lows(closed)
    high_p = find_pivot_highs(closed)
    if len(low_p) < 2 or len(high_p) < 2:
        return False, Decimal("0")
    hl = any(closed[low_p[i]].low > closed[low_p[i-1]].low for i in range(1, len(low_p)))
    hh = any(closed[high_p[i]].high > closed[high_p[i-1]].high for i in range(1, len(high_p)))
    if not (hl and hh):
        return False, Decimal("0")
    return True, closed[low_p[-1]].low


def h4_downtrend_structure(h4: Sequence[Kline]) -> bool:
    """True if H4 shows bearish structure (LH confirmed OR HL broken)."""
    closed = h4[:-1]
    if len(closed) < 20:
        return False
    high_p = find_pivot_highs(closed)
    if len(high_p) >= 2:
        if closed[high_p[-1]].high < closed[high_p[-2]].high:
            return True
    low_p = find_pivot_lows(closed)
    if len(low_p) >= 2:
        recent_hl = closed[low_p[-2]].low
        if closed[-1].close < recent_hl:
            return True
    return False


# ── H1 BOS detection ─────────────────────────────────────────────────────────

def find_h1_upward_bos(h1: Sequence[Kline]) -> BosState | None:
    """Most recent upward BOS in last H1_BOS_LOOKBACK_LONG=12 closed bars."""
    closed = h1[:-1]
    if len(closed) < 20:
        return None
    atr_h1 = atr14(closed)
    vma_h1 = vol_ma20(closed)
    if atr_h1 == 0 or vma_h1 == 0:
        return None
    window = closed[-H1_BOS_LOOKBACK_LONG:]
    for i in range(len(window) - 1, 0, -1):
        bar = window[i]
        if bar.close <= bar.open:
            continue
        prev_high = max(b.high for b in window[:i])
        if bar.close <= prev_high:
            continue
        if bar.close - prev_high < H1_BOS_MIN_AMP_ATR * atr_h1:
            continue
        if bar.volume < IMPULSE_VOL_RATIO * vma_h1:
            continue
        impulse_low  = min(b.low  for b in window[:i + 1])
        impulse_high = bar.close
        return BosState(
            impulse_low=impulse_low,
            bos_level=prev_high,
            impulse_high=impulse_high,
            bos_time_ms=bar.close_time_ms,
            bos_vol_ratio=bar.volume / vma_h1,
        )
    return None


def find_h1_downward_bos(h1: Sequence[Kline]) -> BosState | None:
    """Most recent downward BOS in last H1_BOS_LOOKBACK_SHORT=8 closed bars."""
    closed = h1[:-1]
    if len(closed) < 20:
        return None
    atr_h1 = atr14(closed)
    vma_h1 = vol_ma20(closed)
    if atr_h1 == 0 or vma_h1 == 0:
        return None
    window = closed[-H1_BOS_LOOKBACK_SHORT:]
    for i in range(len(window) - 1, 0, -1):
        bar = window[i]
        if bar.close >= bar.open:
            continue
        prev_low = min(b.low for b in window[:i])
        if bar.close >= prev_low:
            continue
        if prev_low - bar.close < H1_BOS_MIN_AMP_ATR * atr_h1:
            continue
        if bar.volume < IMPULSE_VOL_RATIO * vma_h1:
            continue
        impulse_high = max(b.high for b in window[:i + 1])
        impulse_low  = bar.close
        return BosState(
            impulse_low=impulse_low,
            bos_level=prev_low,
            impulse_high=impulse_high,
            bos_time_ms=bar.close_time_ms,
            bos_vol_ratio=bar.volume / vma_h1,
        )
    return None


# ── M15 divergence scanners ───────────────────────────────────────────────────

def find_bullish_divergence(
    klines: Sequence[Kline],
    rsi_vals: Sequence[Decimal | None],
    atr_val: Decimal,
    vma: Decimal,
    bos_time_ms: int,
    impulse_high: Decimal,
    impulse_low: Decimal,
    bos_level: Decimal,
) -> DivergenceResult | None:
    """Bullish divergence: price LL + RSI HL in M15 bars after BOS.

    Returns most-recent trigger or None.
    """
    post_bos = [i for i, k in enumerate(klines) if k.close_time_ms > bos_time_ms]
    if len(post_bos) < DIVERGENCE_MIN_BARS + PIVOT_RIGHT + 2:
        return None
    start = post_bos[0]
    pivot_idxs = [i for i in find_pivot_lows(klines) if i >= start]
    if len(pivot_idxs) < 2:
        return None
    impulse_range = impulse_high - impulse_low
    if impulse_range <= 0:
        return None

    for j in range(len(pivot_idxs) - 1, 0, -1):
        idx2 = pivot_idxs[j]
        for k_idx in range(j - 1, -1, -1):
            idx1 = pivot_idxs[k_idx]
            if idx2 - idx1 < DIVERGENCE_MIN_BARS or idx2 - idx1 > DIVERGENCE_MAX_BARS:
                continue
            price1 = klines[idx1].low
            price2 = klines[idx2].low
            rsi1, rsi2 = rsi_vals[idx1], rsi_vals[idx2]
            if rsi1 is None or rsi2 is None:
                continue
            if price2 >= price1 - PRICE_MIN_DIFF_ATR * atr_val:
                continue
            if rsi2 <= rsi1 + RSI_MIN_DIFFERENCE:
                continue
            if rsi1 >= 50 or rsi2 >= 50:
                continue
            if rsi1 > 40 and rsi2 > 40:
                continue
            pullback = (impulse_high - price2) / impulse_range
            if not (MIN_PULLBACK_RATIO <= pullback <= MAX_PULLBACK_RATIO):
                continue
            zone_lo = bos_level - PULLBACK_ZONE_TOL_ATR * atr_val
            zone_hi = bos_level + PULLBACK_ZONE_TOL_ATR * atr_val
            if not (zone_lo <= price2 <= zone_hi):
                continue
            internal_sh = max(klines[m].high for m in range(idx1, idx2 + 1))
            trigger_start = idx2 + PIVOT_RIGHT
            for t in range(len(klines) - 1, trigger_start - 1, -1):
                bar = klines[t]
                if bar.close <= internal_sh or bar.close <= bar.open:
                    continue
                pb_range = list(range(max(idx2, t - 3), t))
                pb_vols  = [klines[m].volume for m in pb_range] if pb_range else [klines[idx2].volume]
                vol_pb   = sum(pb_vols) / Decimal(len(pb_vols))
                if vma > 0 and vol_pb > PULLBACK_VOL_RATIO_MAX * vma:
                    continue
                if vma > 0 and bar.volume < CONFIRM_VOL_RATIO * vma:
                    continue
                return DivergenceResult(
                    pivot1_idx=idx1, pivot1_price=price1, pivot1_rsi=rsi1,
                    pivot2_idx=idx2, pivot2_price=price2, pivot2_rsi=rsi2,
                    internal_key=internal_sh, trigger_close=bar.close,
                    trigger_idx=t,
                    vol_pullback=vol_pb / vma if vma > 0 else Decimal("0"),
                    vol_confirm=bar.volume / vma if vma > 0 else Decimal("0"),
                )
    return None


def find_bearish_divergence(
    klines: Sequence[Kline],
    rsi_vals: Sequence[Decimal | None],
    atr_val: Decimal,
    vma: Decimal,
    bos_time_ms: int,
    impulse_high: Decimal,
    impulse_low: Decimal,
    bos_level: Decimal,
) -> DivergenceResult | None:
    """Bearish divergence: price HH + RSI LH in M15 bars after BOS."""
    post_bos = [i for i, k in enumerate(klines) if k.close_time_ms > bos_time_ms]
    if len(post_bos) < DIVERGENCE_MIN_BARS + PIVOT_RIGHT + 2:
        return None
    start = post_bos[0]
    pivot_idxs = [i for i in find_pivot_highs(klines) if i >= start]
    if len(pivot_idxs) < 2:
        return None
    impulse_range = impulse_high - impulse_low
    if impulse_range <= 0:
        return None

    for j in range(len(pivot_idxs) - 1, 0, -1):
        idx2 = pivot_idxs[j]
        for k_idx in range(j - 1, -1, -1):
            idx1 = pivot_idxs[k_idx]
            if idx2 - idx1 < DIVERGENCE_MIN_BARS or idx2 - idx1 > DIVERGENCE_MAX_BARS:
                continue
            price1 = klines[idx1].high
            price2 = klines[idx2].high
            rsi1, rsi2 = rsi_vals[idx1], rsi_vals[idx2]
            if rsi1 is None or rsi2 is None:
                continue
            if price2 <= price1 + PRICE_MIN_DIFF_ATR * atr_val:
                continue
            if rsi2 >= rsi1 - RSI_MIN_DIFFERENCE:
                continue
            if rsi1 <= 50 or rsi2 <= 50:
                continue
            if rsi1 < 60 and rsi2 < 60:
                continue
            rebound = (price2 - impulse_low) / impulse_range
            if not (MIN_PULLBACK_RATIO <= rebound <= MAX_PULLBACK_RATIO):
                continue
            zone_lo = bos_level - PULLBACK_ZONE_TOL_ATR * atr_val
            zone_hi = bos_level + PULLBACK_ZONE_TOL_ATR * atr_val
            if not (zone_lo <= price2 <= zone_hi):
                continue
            internal_sl = min(klines[m].low for m in range(idx1, idx2 + 1))
            trigger_start = idx2 + PIVOT_RIGHT
            for t in range(len(klines) - 1, trigger_start - 1, -1):
                bar = klines[t]
                if bar.close >= internal_sl or bar.close >= bar.open:
                    continue
                pb_range = list(range(max(idx2, t - 3), t))
                pb_vols  = [klines[m].volume for m in pb_range] if pb_range else [klines[idx2].volume]
                vol_pb   = sum(pb_vols) / Decimal(len(pb_vols))
                if vma > 0 and vol_pb > PULLBACK_VOL_RATIO_MAX * vma:
                    continue
                if vma > 0 and bar.volume < CONFIRM_VOL_RATIO * vma:
                    continue
                return DivergenceResult(
                    pivot1_idx=idx1, pivot1_price=price1, pivot1_rsi=rsi1,
                    pivot2_idx=idx2, pivot2_price=price2, pivot2_rsi=rsi2,
                    internal_key=internal_sl, trigger_close=bar.close,
                    trigger_idx=t,
                    vol_pullback=vol_pb / vma if vma > 0 else Decimal("0"),
                    vol_confirm=bar.volume / vma if vma > 0 else Decimal("0"),
                )
    return None


# ── Available R check ─────────────────────────────────────────────────────────

def nearest_h1_resistance(h1: Sequence[Kline], above: Decimal) -> Decimal | None:
    """Nearest H1 pivot HIGH strictly above `above` price (last 30 closed bars)."""
    closed = h1[:-1]
    window = closed[-30:] if len(closed) > 30 else closed
    pivots = find_pivot_highs(window)
    candidates = [window[i].high for i in pivots if window[i].high > above]
    return min(candidates) if candidates else None


def nearest_h1_support(h1: Sequence[Kline], below: Decimal) -> Decimal | None:
    """Nearest H1 pivot LOW strictly below `below` price (last 30 closed bars)."""
    closed = h1[:-1]
    window = closed[-30:] if len(closed) > 30 else closed
    pivots = find_pivot_lows(window)
    candidates = [window[i].low for i in pivots if window[i].low < below]
    return max(candidates) if candidates else None
