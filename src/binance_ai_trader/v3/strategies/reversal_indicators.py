"""Indicator helpers for the hotlist_reversal (V-Reversal) strategy.

All functions take plain lists of Decimals/floats derived from Kline tuples
(oldest → newest) and are pure/stateless so they are trivially unit-testable.
"""
from __future__ import annotations

from decimal import Decimal


def atr(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], period: int = 14) -> Decimal | None:
    """Wilder's ATR (simple-average variant, sufficient for our threshold use)."""
    if len(highs) < period + 1:
        return None
    trs: list[Decimal] = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    window = trs[-period:]
    return sum(window) / Decimal(len(window))


def rsi(closes: list[Decimal], period: int = 14) -> Decimal | None:
    if len(closes) < period + 1:
        return None
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(delta if delta > 0 else Decimal(0))
        losses.append(-delta if delta < 0 else Decimal(0))
    avg_gain = sum(gains[-period:]) / Decimal(period)
    avg_loss = sum(losses[-period:]) / Decimal(period)
    if avg_loss == 0:
        return Decimal(100)
    rs = avg_gain / avg_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))


def bollinger(closes: list[Decimal], period: int = 20, num_std: Decimal = Decimal("3")) -> tuple[Decimal, Decimal, Decimal] | None:
    """Returns (mid, upper, lower) using `num_std` standard deviations."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / Decimal(period)
    variance = sum((c - mid) ** 2 for c in window) / Decimal(period)
    std = variance.sqrt()
    return mid, mid + num_std * std, mid - num_std * std


def wick_ratio(open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> tuple[Decimal, Decimal]:
    """Returns (upper_wick_ratio, lower_wick_ratio) as fraction of full range [0,1]."""
    full_range = high - low
    if full_range <= 0:
        return Decimal(0), Decimal(0)
    body_top = max(open_, close)
    body_bottom = min(open_, close)
    upper_wick = high - body_top
    lower_wick = body_bottom - low
    return upper_wick / full_range, lower_wick / full_range


def volume_ma(volumes: list[Decimal], period: int = 20) -> Decimal | None:
    if len(volumes) < period + 1:
        return None
    window = volumes[-(period + 1):-1]  # exclude current bar
    return sum(window) / Decimal(len(window))


def oi_drop_pct(oi_points: list[Decimal]) -> Decimal | None:
    """Percent drop from the earliest to the latest of the given OI points
    (expects 3 x 5-minute points spanning ~15 minutes, oldest → newest).
    Negative return means OI fell (what we want); positive means it rose.
    """
    if len(oi_points) < 2 or oi_points[0] == 0:
        return None
    return (oi_points[-1] - oi_points[0]) / oi_points[0] * Decimal(100)
