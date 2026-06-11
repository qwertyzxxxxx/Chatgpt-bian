from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegimeSignalGate:
    range_minimum_score: float = 85.0

    def allows_long(self, combined_regime: str, score: float) -> bool:
        if combined_regime == "BULL":
            return True
        if combined_regime == "RANGE":
            return score >= self.range_minimum_score
        return False

    def allows_short(self, combined_regime: str, weakness_score: float) -> bool:
        if combined_regime == "BEAR":
            return True
        if combined_regime == "RANGE":
            return weakness_score >= self.range_minimum_score
        return False

    def allowed_directions(
        self, combined_regime: str, long_score: float, weakness_score: float
    ) -> tuple[str, ...]:
        result = []
        if self.allows_long(combined_regime, long_score):
            result.append("LONG")
        if self.allows_short(combined_regime, weakness_score):
            result.append("SHORT")
        return tuple(result)
