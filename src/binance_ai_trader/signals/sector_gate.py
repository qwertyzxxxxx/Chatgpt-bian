from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SectorSignalGate:
    medium_minimum_score: float = 85.0
    weak_minimum_score: float = 90.0

    def allows_long(
        self,
        sector: str,
        sector_rank: int | None,
        score: float,
        snapshots_available: bool,
    ) -> bool:
        if not snapshots_available:
            return True
        if sector == "OTHER" or sector_rank is None or sector_rank > 6:
            return score >= self.weak_minimum_score
        if sector_rank <= 3:
            return True
        return score >= self.medium_minimum_score
