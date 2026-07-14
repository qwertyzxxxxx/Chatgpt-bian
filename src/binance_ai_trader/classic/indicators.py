"""Technical indicators for Classic strategies.

All functions operate on tuples of already-closed Kline objects.
Never pass the currently-forming candle into these functions.
"""
from __future__ import annotations

import statistics
from decimal import Decimal

from binance_ai_trader.domain.models import Kline


# ─────────────────────────────────────────────────────────────────────────────
# Moving averages
# ─────────────────────────────────────────────────────────────────────────────

def ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    """Exponential moving average (smoothing = 2/(N+1))."""
    if not values:
        raise ValueError("empty values for EMA")
    k = Decimal(2) / Decimal(period + 1)
    result = values[0]
    for v in values[1:]:
        result = v * k + result * (1 - k)
    return result


def ema_from_klines(klines: tuple[Kline, ...], period: int) -> Decimal:
    closes = tuple(k.close for k in klines)
    return ema(closes, period)


def ema_direction_up(klines: tuple[Kline, ...], period: int, lookback: int = 5) -> bool:
    """Return True if EMA is trending upward over the last `lookback` bars."""
    if len(klines) < period + lookback:
        return False
    recent = tuple(k.close for k in klines)
    ema_now = ema(recent, period)
    ema_prev = ema(tuple(k.close for k in klines[:-lookback]), period)
    return ema_now > ema_prev


# ─────────────────────────────────────────────────────────────────────────────
# ATR
# ─────────────────────────────────────────────────────────────────────────────

def atr(klines: tuple[Kline, ...], period: int = 14) -> Decimal:
    """Average True Range."""
    if len(klines) < period + 1:
        raise ValueError(f"need ≥{period+1} klines for ATR{period}, got {len(klines)}")
    trs: list[Decimal] = []
    for prev, cur in zip(klines[-(period + 1):-1], klines[-period:]):
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        )
        trs.append(tr)
    return sum(trs) / Decimal(period)


# ─────────────────────────────────────────────────────────────────────────────
# Volume analysis
# ─────────────────────────────────────────────────────────────────────────────

def vol_ratio(klines: tuple[Kline, ...], lookback: int = 20) -> Decimal:
    """Current (last closed) bar vol / median of previous `lookback` bars."""
    if len(klines) < lookback + 1:
        return Decimal("0")
    prev_vols = [float(k.quote_volume) for k in klines[-(lookback + 1):-1]]
    med = statistics.median(prev_vols)
    if med == 0:
        return Decimal("0")
    return klines[-1].quote_volume / Decimal(str(med))


def vol_ratio_for_segment(klines: tuple[Kline, ...], lookback: int = 20) -> Decimal:
    """Average vol_ratio across a segment (used for multi-bar rally/decline check)."""
    if not klines:
        return Decimal("0")
    ratios = []
    for i in range(len(klines)):
        window = klines[max(0, i - lookback):i]
        if not window:
            continue
        prev_vols = [float(k.quote_volume) for k in window]
        med = statistics.median(prev_vols) if prev_vols else 0
        if med == 0:
            continue
        ratios.append(float(klines[i].quote_volume) / med)
    if not ratios:
        return Decimal("0")
    return Decimal(str(statistics.median(ratios)))


def close_location(k: Kline) -> Decimal:
    """(close - low) / (high - low). Returns 0 if high == low."""
    rng = k.high - k.low
    if rng == 0:
        return Decimal("0")
    return (k.close - k.low) / rng


def body_ratio(k: Kline) -> Decimal:
    rng = k.high - k.low
    if rng == 0:
        return Decimal("0")
    return abs(k.close - k.open) / rng


def upper_wick_ratio(k: Kline) -> Decimal:
    rng = k.high - k.low
    if rng == 0:
        return Decimal("0")
    return (k.high - max(k.open, k.close)) / rng


def lower_wick_ratio(k: Kline) -> Decimal:
    rng = k.high - k.low
    if rng == 0:
        return Decimal("0")
    return (min(k.open, k.close) - k.low) / rng


# ─────────────────────────────────────────────────────────────────────────────
# Price structure helpers
# ─────────────────────────────────────────────────────────────────────────────

def swing_lows(klines: tuple[Kline, ...], window: int = 3) -> list[Decimal]:
    """Local swing lows: candle whose low < all neighbors within `window`."""
    lows: list[Decimal] = []
    n = len(klines)
    for i in range(window, n - window):
        lo = klines[i].low
        if all(klines[i - j].low >= lo for j in range(1, window + 1)) and \
           all(klines[i + j].low >= lo for j in range(1, window + 1)):
            lows.append(lo)
    return lows


def swing_highs(klines: tuple[Kline, ...], window: int = 3) -> list[Decimal]:
    """Local swing highs: candle whose high > all neighbors within `window`."""
    highs: list[Decimal] = []
    n = len(klines)
    for i in range(window, n - window):
        hi = klines[i].high
        if all(klines[i - j].high <= hi for j in range(1, window + 1)) and \
           all(klines[i + j].high <= hi for j in range(1, window + 1)):
            highs.append(hi)
    return highs


def has_higher_low(klines: tuple[Kline, ...], window: int = 3) -> bool:
    """Returns True if the most recent swing low is higher than the previous one."""
    lows = swing_lows(klines, window)
    return len(lows) >= 2 and lows[-1] > lows[-2]


def has_lower_high(klines: tuple[Kline, ...], window: int = 3) -> bool:
    """Returns True if the most recent swing high is lower than the previous one."""
    highs = swing_highs(klines, window)
    return len(highs) >= 2 and highs[-1] < highs[-2]


def struct_low(klines: tuple[Kline, ...], lookback: int = 10) -> Decimal:
    """Recent structure low = min low of last `lookback` bars."""
    return min(k.low for k in klines[-lookback:])


def struct_high(klines: tuple[Kline, ...], lookback: int = 10) -> Decimal:
    """Recent structure high = max high of last `lookback` bars."""
    return max(k.high for k in klines[-lookback:])


# ─────────────────────────────────────────────────────────────────────────────
# Platform / consolidation detection
# ─────────────────────────────────────────────────────────────────────────────

def platform_stats(klines: tuple[Kline, ...], bars: int = 20) -> dict:
    """
    Analyse the last `bars` 1H candles for a consolidation platform.
    Returns dict with: high, low, range_pct, high_tests, low_tests, is_tight.
    """
    seg = klines[-bars:]
    hi = max(k.high for k in seg)
    lo = min(k.low for k in seg)
    rng_pct = float((hi - lo) / lo * 100) if lo > 0 else 999.0
    tol = (hi - lo) * Decimal("0.10")   # 10% of range for "near the high/low"
    high_tests = sum(1 for k in seg if k.high >= hi - tol)
    low_tests  = sum(1 for k in seg if k.low <= lo + tol)
    return {
        "high": hi, "low": lo,
        "range_pct": rng_pct,
        "high_tests": high_tests,
        "low_tests": low_tests,
        "is_tight": rng_pct <= 8.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Daily / multi-day metrics
# ─────────────────────────────────────────────────────────────────────────────

def change_nd(daily: tuple[Kline, ...], n: int) -> Decimal:
    """Return n-day price change % using the last n+1 closed daily bars."""
    if len(daily) < n + 1:
        return Decimal("0")
    start = daily[-(n + 1)].close
    end   = daily[-1].close
    if start == 0:
        return Decimal("0")
    return (end - start) / start * 100


def range_position_30d(daily: tuple[Kline, ...]) -> Decimal:
    """(current_price - low_30d) / (high_30d - low_30d). 0..1."""
    n = min(30, len(daily))
    seg = daily[-n:]
    hi = max(k.high for k in seg)
    lo = min(k.low for k in seg)
    current = seg[-1].close
    if hi == lo:
        return Decimal("0.5")
    return (current - lo) / (hi - lo)


def consecutive_trend_days(daily: tuple[Kline, ...]) -> tuple[int, str]:
    """
    Count consecutive up or down days ending at the last bar.
    Returns (count, 'UP'|'DOWN'|'FLAT').
    """
    if len(daily) < 2:
        return 0, "FLAT"
    direction = "UP" if daily[-1].close > daily[-1].open else "DOWN"
    count = 1
    for k in reversed(daily[:-1]):
        d = "UP" if k.close > k.open else "DOWN"
        if d == direction:
            count += 1
        else:
            break
    return count, direction


def vol_grade(ratio: Decimal) -> str:
    from binance_ai_trader.classic.config import CFG
    r = ratio
    if r >= CFG.vol_grade_s_plus_min:
        return "S_PLUS"
    if r >= CFG.vol_grade_s_min:
        return "S"
    if r >= CFG.vol_grade_a_min:
        return "A"
    if r >= CFG.vol_grade_normal_min:
        return "NORMAL"
    return "WEAK"
