from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

from binance_ai_trader.domain.models import Kline


@dataclass(frozen=True, slots=True)
class ComponentScore:
    score: float
    maximum: float
    metrics: dict[str, float | int | bool]

    def as_dict(self) -> dict[str, object]:
        return {
            "score": round(self.score, 2),
            "max_score": self.maximum,
            "metrics": self.metrics,
        }


def closes(klines: Sequence[Kline]) -> list[float]:
    return [float(item.close) for item in klines]


def ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"EMA{period} requires at least {period} values")
    alpha = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) <= period:
        raise ValueError(f"RSI{period} requires at least {period + 1} values")
    changes = [current - previous for previous, current in zip(values, values[1:])]
    seed = changes[:period]
    average_gain = sum(max(change, 0.0) for change in seed) / period
    average_loss = sum(max(-change, 0.0) for change in seed) / period
    for change in changes[period:]:
        average_gain = ((period - 1) * average_gain + max(change, 0.0)) / period
        average_loss = ((period - 1) * average_loss + max(-change, 0.0)) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + average_gain / average_loss))


def true_ranges(klines: Sequence[Kline]) -> list[float]:
    if not klines:
        return []
    result: list[float] = []
    previous_close = float(klines[0].close)
    for item in klines:
        high = float(item.high)
        low = float(item.low)
        result.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = float(item.close)
    return result


def bounded(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0:
        return default
    result = numerator / denominator
    return result if isfinite(result) else default
