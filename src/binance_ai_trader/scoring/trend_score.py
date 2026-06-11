from __future__ import annotations

from collections.abc import Sequence

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.scoring.common import ComponentScore, closes, ema


class TrendScore:
    maximum = 30.0

    def calculate(self, hourly: Sequence[Kline], four_hourly: Sequence[Kline]) -> ComponentScore:
        hourly_closes = closes(hourly)
        four_hour_closes = closes(four_hourly)
        h1_ema20 = ema(hourly_closes, 20)
        h1_ema50 = ema(hourly_closes, 50)
        h4_ema20 = ema(four_hour_closes, 20)
        h4_ema50 = ema(four_hour_closes, 50)
        previous_h4_ema20 = ema(four_hour_closes[:-5], 20)

        rules = {
            "h4_close_above_ema20": four_hour_closes[-1] > h4_ema20,
            "h4_ema20_above_ema50": h4_ema20 > h4_ema50,
            "h4_ema20_rising": h4_ema20 > previous_h4_ema20,
            "h1_close_above_ema20": hourly_closes[-1] > h1_ema20,
            "h1_ema20_above_ema50": h1_ema20 > h1_ema50,
        }
        weights = (8.0, 8.0, 6.0, 4.0, 4.0)
        score = sum(weight for weight, passed in zip(weights, rules.values()) if passed)
        return ComponentScore(
            score,
            self.maximum,
            {
                **rules,
                "h1_ema20": round(h1_ema20, 8),
                "h1_ema50": round(h1_ema50, 8),
                "h4_ema20": round(h4_ema20, 8),
                "h4_ema50": round(h4_ema50, 8),
            },
        )
