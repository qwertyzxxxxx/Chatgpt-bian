from __future__ import annotations

from collections.abc import Sequence

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.scoring.common import ComponentScore, bounded, safe_ratio


class VolumeScore:
    maximum = 20.0

    def calculate(self, fifteen_minute: Sequence[Kline], hourly: Sequence[Kline]) -> ComponentScore:
        ratio_15m = self._recent_ratio(fifteen_minute, 8, 32)
        ratio_1h = self._recent_ratio(hourly, 6, 24)
        trade_ratio = self._trade_ratio(fifteen_minute, 8, 32)
        score_15m = bounded((ratio_15m - 0.6) / 1.0, 0.0, 1.0) * 8.0
        score_1h = bounded((ratio_1h - 0.6) / 1.0, 0.0, 1.0) * 8.0
        score_trades = bounded((trade_ratio - 0.6) / 1.0, 0.0, 1.0) * 4.0
        return ComponentScore(
            score_15m + score_1h + score_trades,
            self.maximum,
            {
                "quote_volume_ratio_15m": round(ratio_15m, 4),
                "quote_volume_ratio_1h": round(ratio_1h, 4),
                "trade_count_ratio_15m": round(trade_ratio, 4),
            },
        )

    @staticmethod
    def _recent_ratio(klines: Sequence[Kline], recent: int, baseline: int) -> float:
        values = [float(item.quote_volume) for item in klines]
        recent_average = sum(values[-recent:]) / recent
        baseline_values = values[-(recent + baseline):-recent]
        return safe_ratio(recent_average, sum(baseline_values) / len(baseline_values))

    @staticmethod
    def _trade_ratio(klines: Sequence[Kline], recent: int, baseline: int) -> float:
        values = [item.trade_count for item in klines]
        recent_average = sum(values[-recent:]) / recent
        baseline_values = values[-(recent + baseline):-recent]
        return safe_ratio(recent_average, sum(baseline_values) / len(baseline_values))
