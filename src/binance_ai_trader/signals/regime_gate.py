from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegimeSignalGate:
    range_minimum_score: float = 80.0

    def allows_long(self, combined_regime: str, score: float) -> bool:
        if combined_regime == "BULL":
            return True
        if combined_regime == "RANGE":
            return score >= self.range_minimum_score
        return False
