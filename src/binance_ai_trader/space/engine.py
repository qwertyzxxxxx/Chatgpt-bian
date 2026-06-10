from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from binance_ai_trader.domain.models import Kline


@dataclass(frozen=True, slots=True)
class SpaceSnapshot:
    run_id: str
    symbol: str
    direction: str
    high_distance_30d_pct: Decimal
    high_distance_60d_pct: Decimal
    high_distance_120d_pct: Decimal
    low_distance_30d_pct: Decimal
    low_distance_60d_pct: Decimal
    low_distance_120d_pct: Decimal
    upside_pct: Decimal
    downside_pct: Decimal
    space_score: Decimal
    data_quality_status: str = "COMPLETE"


class SpaceEngine:
    """Measure directional room using closed 4h highs/lows over 30/60/120 days."""

    REQUIRED_4H_BARS = 720

    def score(
        self, run_id: str, symbol: str, direction: str, bars: Sequence[Kline]
    ) -> SpaceSnapshot:
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        if len(bars) < self.REQUIRED_4H_BARS:
            raise ValueError("720 closed 4h bars are required for 120d space")
        window = tuple(bars[-self.REQUIRED_4H_BARS:])
        latest = window[-1].close
        highs = tuple(max(item.high for item in window[-count:]) for count in (180, 360, 720))
        lows = tuple(min(item.low for item in window[-count:]) for count in (180, 360, 720))
        high_distances = tuple((value - latest) / latest * 100 for value in highs)
        low_distances = tuple((latest - value) / latest * 100 for value in lows)
        upside = max(high_distances)
        downside = max(low_distances)
        directional = upside if direction == "LONG" else downside
        score = max(Decimal("0"), min(Decimal("100"), directional * 5))
        return SpaceSnapshot(
            run_id=run_id, symbol=symbol, direction=direction,
            high_distance_30d_pct=_two(high_distances[0]), high_distance_60d_pct=_two(high_distances[1]),
            high_distance_120d_pct=_two(high_distances[2]), low_distance_30d_pct=_two(low_distances[0]),
            low_distance_60d_pct=_two(low_distances[1]), low_distance_120d_pct=_two(low_distances[2]),
            upside_pct=_two(upside), downside_pct=_two(downside), space_score=_two(score),
        )


def _two(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
