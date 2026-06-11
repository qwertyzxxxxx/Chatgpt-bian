from __future__ import annotations


def final_signal_score(
    *, capital_score: float, space_score: float, trend_score: float,
    sector_rank: int | None, combined_regime: str, direction: str,
) -> float:
    """Return the deterministic V2 ranking score without changing trade prices."""
    directional_trend = trend_score if direction == "LONG" else 100.0 - trend_score
    sector_score = _sector_score(sector_rank, direction)
    regime_score = 70.0 if combined_regime == "RANGE" else 100.0
    return round(
        capital_score * 0.30 + space_score * 0.30 + directional_trend * 0.20
        + sector_score * 0.10 + regime_score * 0.10,
        2,
    )


def _sector_score(rank: int | None, direction: str) -> float:
    if rank is None:
        return 50.0
    if direction == "SHORT":
        if rank <= 3:
            return 40.0
        if rank <= 6:
            return 70.0
        return 100.0
    if rank <= 3:
        return 100.0
    if rank <= 6:
        return 70.0
    return 40.0
