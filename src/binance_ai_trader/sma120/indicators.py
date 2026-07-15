"""Indicator helpers for SMA120 V1.9-D (EMA, SMA, ATR)."""
from __future__ import annotations

from decimal import Decimal


def ema_series(values: list[Decimal], period: int) -> list[Decimal]:
    """Return EMA series seeded with SMA.

    Output length = len(values) - period + 1.
    values must be in chronological order (oldest first).
    """
    if len(values) < period:
        raise ValueError(f"Not enough data for EMA{period}: need {period}, have {len(values)}")
    k = Decimal(2) / Decimal(period + 1)
    ema_val = sum(values[:period]) / Decimal(period)
    result = [ema_val]
    for v in values[period:]:
        ema_val = v * k + ema_val * (1 - k)
        result.append(ema_val)
    return result


def ema_last(values: list[Decimal], period: int) -> Decimal:
    """Most recent EMA value."""
    return ema_series(values, period)[-1]


def ema_last_two(values: list[Decimal], period: int) -> tuple[Decimal, Decimal]:
    """Return (current_ema, previous_ema). Requires len(values) >= period + 1."""
    s = ema_series(values, period)
    if len(s) < 2:
        raise ValueError(f"Need at least {period + 1} values to get two EMA readings")
    return s[-1], s[-2]


def sma_last(values: list[Decimal], period: int) -> Decimal:
    """Simple moving average of the last *period* values."""
    if len(values) < period:
        raise ValueError(f"Not enough data for SMA{period}: need {period}, have {len(values)}")
    return sum(values[-period:]) / Decimal(period)


def atr_last(
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    period: int = 14,
) -> Decimal:
    """ATR using Wilder's smoothing. Requires len(closes) >= period + 1."""
    if len(closes) < period + 1:
        raise ValueError(f"Not enough data for ATR{period}: need {period + 1}, have {len(closes)}")

    trs: list[Decimal] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    atr_val = sum(trs[:period]) / Decimal(period)
    for tr in trs[period:]:
        atr_val = (atr_val * Decimal(period - 1) + tr) / Decimal(period)
    return atr_val
