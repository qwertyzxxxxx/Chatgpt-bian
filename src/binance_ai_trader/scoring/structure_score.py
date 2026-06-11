from __future__ import annotations

from collections.abc import Sequence

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.scoring.common import ComponentScore, bounded, safe_ratio


class StructureScore:
    maximum = 15.0

    def calculate(self, fifteen_minute: Sequence[Kline], hourly: Sequence[Kline]) -> ComponentScore:
        recent_1h = hourly[-20:]
        prior_1h = hourly[-40:-20]
        current_high = max(float(item.high) for item in recent_1h)
        current_low = min(float(item.low) for item in recent_1h)
        prior_high = max(float(item.high) for item in prior_1h)
        prior_low = min(float(item.low) for item in prior_1h)
        close = float(hourly[-1].close)
        range_position = safe_ratio(close - current_low, current_high - current_low, 0.5)
        higher_high = current_high > prior_high
        higher_low = current_low > prior_low
        breakout_15m = float(fifteen_minute[-1].close) > max(float(item.high) for item in fifteen_minute[-21:-1])
        score = bounded(range_position, 0.0, 1.0) * 5.0
        score += 4.0 if higher_high else 0.0
        score += 4.0 if higher_low else 0.0
        score += 2.0 if breakout_15m else 0.0
        return ComponentScore(
            score,
            self.maximum,
            {
                "range_position_1h": round(range_position, 4),
                "higher_high_1h": higher_high,
                "higher_low_1h": higher_low,
                "breakout_15m": breakout_15m,
            },
        )
