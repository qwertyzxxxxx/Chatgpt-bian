from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True, slots=True)
class CapitalInputs:
    symbol: str
    quote_volume_24h: Decimal
    average_quote_volume_24h: Decimal
    oi_current: Decimal
    oi_1h_ago: Decimal
    oi_4h_ago: Decimal
    oi_24h_ago: Decimal
    current_funding_rate: Decimal
    long_short_ratio: Decimal


@dataclass(frozen=True, slots=True)
class CapitalSnapshot:
    run_id: str
    symbol: str
    oi_current: Decimal
    oi_change_1h_pct: Decimal
    oi_change_4h_pct: Decimal
    oi_change_24h_pct: Decimal
    current_funding_rate: Decimal
    funding_score: Decimal
    long_short_ratio: Decimal
    crowding_score: Decimal
    volume_expansion_score: Decimal
    oi_expansion_score: Decimal
    capital_score: Decimal


class CapitalFlowEngine:
    """Deterministic 0-100 public-market capital-flow score."""

    def score(self, run_id: str, inputs: CapitalInputs) -> CapitalSnapshot:
        changes = tuple(
            _change(inputs.oi_current, previous)
            for previous in (inputs.oi_1h_ago, inputs.oi_4h_ago, inputs.oi_24h_ago)
        )
        volume_score = _clamp(50 + _change(inputs.quote_volume_24h, inputs.average_quote_volume_24h) * 5)
        weighted_oi = changes[0] * Decimal("0.2") + changes[1] * Decimal("0.3") + changes[2] * Decimal("0.5")
        oi_score = _clamp(50 + weighted_oi * 4)
        funding_score = _funding_score(inputs.current_funding_rate)
        crowding_score = _crowding_score(inputs.long_short_ratio)
        capital = (
            volume_score * Decimal("0.30")
            + oi_score * Decimal("0.35")
            + funding_score * Decimal("0.20")
            + crowding_score * Decimal("0.15")
        )
        return CapitalSnapshot(
            run_id=run_id, symbol=inputs.symbol, oi_current=inputs.oi_current,
            oi_change_1h_pct=_two(changes[0]), oi_change_4h_pct=_two(changes[1]),
            oi_change_24h_pct=_two(changes[2]), current_funding_rate=inputs.current_funding_rate,
            funding_score=_two(funding_score), long_short_ratio=inputs.long_short_ratio,
            crowding_score=_two(crowding_score), volume_expansion_score=_two(volume_score),
            oi_expansion_score=_two(oi_score), capital_score=_two(capital),
        )


def _funding_score(rate: Decimal) -> Decimal:
    absolute = abs(rate)
    if absolute <= Decimal("0.0001"):
        return Decimal("100")
    if absolute >= Decimal("0.001"):
        return Decimal("0")
    return _clamp(100 - (absolute - Decimal("0.0001")) / Decimal("0.0009") * 100)


def _crowding_score(ratio: Decimal) -> Decimal:
    if ratio <= 0:
        return Decimal("0")
    deviation = abs(ratio - Decimal("1"))
    return _clamp(100 - deviation / Decimal("1.5") * 100)


def _change(current: Decimal, previous: Decimal) -> Decimal:
    return Decimal("0") if previous <= 0 else (current - previous) / previous * 100


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value))


def _two(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
