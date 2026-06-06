from __future__ import annotations

from collections.abc import Sequence

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.scoring.common import ComponentScore, bounded, closes, rsi, safe_ratio


class MomentumScore:
    maximum = 20.0

    def calculate(self, hourly: Sequence[Kline], four_hourly: Sequence[Kline]) -> ComponentScore:
        hourly_closes = closes(hourly)
        four_hour_closes = closes(four_hourly)
        roc_1h = (safe_ratio(hourly_closes[-1], hourly_closes[-7], 1.0) - 1.0) * 100.0
        roc_4h = (safe_ratio(four_hour_closes[-1], four_hour_closes[-7], 1.0) - 1.0) * 100.0
        rsi_1h = rsi(hourly_closes)
        roc_1h_score = bounded((roc_1h + 2.0) / 6.0, 0.0, 1.0) * 7.0
        roc_4h_score = bounded((roc_4h + 4.0) / 12.0, 0.0, 1.0) * 7.0
        if 55.0 <= rsi_1h <= 70.0:
            rsi_score = 6.0
        elif 50.0 <= rsi_1h < 55.0 or 70.0 < rsi_1h <= 75.0:
            rsi_score = 4.0
        elif 45.0 <= rsi_1h < 50.0:
            rsi_score = 2.0
        else:
            rsi_score = 0.0
        return ComponentScore(
            roc_1h_score + roc_4h_score + rsi_score,
            self.maximum,
            {"roc_1h_6_period_pct": round(roc_1h, 4), "roc_4h_6_period_pct": round(roc_4h, 4), "rsi_1h": round(rsi_1h, 4)},
        )
