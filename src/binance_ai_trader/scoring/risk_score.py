from __future__ import annotations

from collections.abc import Sequence

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.scoring.common import ComponentScore, safe_ratio, true_ranges


class RiskScore:
    maximum = 15.0

    def calculate(self, fifteen_minute: Sequence[Kline], hourly: Sequence[Kline]) -> ComponentScore:
        recent_hourly = hourly[-15:]
        atr = sum(true_ranges(recent_hourly)[-14:]) / 14.0
        close = float(hourly[-1].close)
        atr_pct = safe_ratio(atr, close) * 100.0
        recent_15m = fifteen_minute[-20:]
        largest_candle_pct = max(
            safe_ratio(float(item.high) - float(item.low), float(item.open)) * 100.0 for item in recent_15m
        )
        maximum_gap_pct = max(
            safe_ratio(abs(float(current.open) - float(previous.close)), float(previous.close)) * 100.0
            for previous, current in zip(recent_15m, recent_15m[1:])
        )
        atr_score = self._band_score(atr_pct, 0.4, 3.0, 0.15, 6.0) * 8.0
        candle_score = self._inverse_score(largest_candle_pct, 1.5, 6.0) * 4.0
        gap_score = self._inverse_score(maximum_gap_pct, 0.5, 3.0) * 3.0
        return ComponentScore(
            atr_score + candle_score + gap_score,
            self.maximum,
            {
                "atr_1h_pct": round(atr_pct, 4),
                "largest_candle_15m_pct": round(largest_candle_pct, 4),
                "maximum_gap_15m_pct": round(maximum_gap_pct, 4),
            },
        )

    @staticmethod
    def _band_score(value: float, ideal_low: float, ideal_high: float, minimum: float, maximum: float) -> float:
        if ideal_low <= value <= ideal_high:
            return 1.0
        if value < ideal_low:
            return max(0.0, (value - minimum) / (ideal_low - minimum))
        return max(0.0, (maximum - value) / (maximum - ideal_high))

    @staticmethod
    def _inverse_score(value: float, ideal_maximum: float, hard_maximum: float) -> float:
        if value <= ideal_maximum:
            return 1.0
        return max(0.0, (hard_maximum - value) / (hard_maximum - ideal_maximum))
