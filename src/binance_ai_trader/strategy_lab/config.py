from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path


_COMPONENTS = ("trend", "volume", "momentum", "structure", "risk")
_REGIMES = ("BULL", "BEAR", "RANGE", "OBSERVE")
_DIRECTIONS = ("LONG", "SHORT")


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    strategy_id: str
    name: str
    description: str
    scoring_weights: dict[str, float]
    range_min_score: float
    sector_medium_min_score: float
    sector_weak_min_score: float
    entry_distance_min_pct: float
    entry_distance_max_pct: float
    max_stop_loss_pct: float
    min_rr_tp2: float
    evaluation_window_bars: int
    enabled_regimes: tuple[str, ...] = _REGIMES
    enabled_directions: tuple[str, ...] = _DIRECTIONS
    capital_score_min: float = 0.0
    capital_score_max: float = 100.0
    space_score_min: float = 0.0

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.strategy_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("strategy_id must be a non-empty slug")
        if set(self.scoring_weights) != set(_COMPONENTS):
            raise ValueError(f"scoring_weights must contain exactly: {', '.join(_COMPONENTS)}")
        if any(value < 0 for value in self.scoring_weights.values()):
            raise ValueError("scoring weights cannot be negative")
        if round(sum(self.scoring_weights.values()), 8) != 100.0:
            raise ValueError("scoring weights must total 100")
        if not 0 <= self.range_min_score <= 100:
            raise ValueError("range_min_score must be between 0 and 100")
        if not 0 <= self.sector_medium_min_score <= 100:
            raise ValueError("sector_medium_min_score must be between 0 and 100")
        if not 0 <= self.sector_weak_min_score <= 100:
            raise ValueError("sector_weak_min_score must be between 0 and 100")
        if self.sector_medium_min_score > self.sector_weak_min_score:
            raise ValueError("sector medium threshold cannot exceed weak threshold")
        if self.entry_distance_min_pct >= self.entry_distance_max_pct:
            raise ValueError("entry distance minimum must be below maximum")
        if self.max_stop_loss_pct <= 0:
            raise ValueError("max_stop_loss_pct must be positive")
        if self.min_rr_tp2 < 1:
            raise ValueError("min_rr_tp2 must be at least 1")
        if not 1 <= self.evaluation_window_bars <= 96:
            raise ValueError("evaluation_window_bars must be between 1 and 96")
        if not self.enabled_regimes or not set(self.enabled_regimes) <= set(_REGIMES):
            raise ValueError(f"enabled_regimes must contain values from: {', '.join(_REGIMES)}")
        if not self.enabled_directions or not set(self.enabled_directions) <= set(_DIRECTIONS):
            raise ValueError(f"enabled_directions must contain values from: {', '.join(_DIRECTIONS)}")
        if not 0 <= self.capital_score_min <= self.capital_score_max <= 100:
            raise ValueError("capital score range must satisfy 0 <= min <= max <= 100")
        if not 0 <= self.space_score_min <= 100:
            raise ValueError("space_score_min must be between 0 and 100")

    @classmethod
    def load(cls, path: Path) -> "StrategyConfig":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "StrategyConfig":
        return cls(
            strategy_id=str(raw["strategy_id"]),
            name=str(raw["name"]),
            description=str(raw["description"]),
            scoring_weights={str(key): float(value) for key, value in dict(raw["scoring_weights"]).items()},
            range_min_score=float(raw["range_min_score"]),
            sector_medium_min_score=float(raw["sector_medium_min_score"]),
            sector_weak_min_score=float(raw["sector_weak_min_score"]),
            entry_distance_min_pct=float(raw["entry_distance_min_pct"]),
            entry_distance_max_pct=float(raw["entry_distance_max_pct"]),
            max_stop_loss_pct=float(raw["max_stop_loss_pct"]),
            min_rr_tp2=float(raw["min_rr_tp2"]),
            evaluation_window_bars=int(raw["evaluation_window_bars"]),
            enabled_regimes=tuple(str(value) for value in raw.get("enabled_regimes", _REGIMES)),
            enabled_directions=tuple(str(value) for value in raw.get("enabled_directions", _DIRECTIONS)),
            capital_score_min=float(raw.get("capital_score_min", 0.0)),
            capital_score_max=float(raw.get("capital_score_max", 100.0)),
            space_score_min=float(raw.get("space_score_min", 0.0)),
        )

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        research_defaults = {
            "enabled_regimes": _REGIMES,
            "enabled_directions": _DIRECTIONS,
            "capital_score_min": 0.0,
            "capital_score_max": 100.0,
            "space_score_min": 0.0,
        }
        for key, default in research_defaults.items():
            if payload[key] == default:
                payload.pop(key)
        return payload

    def candidate(self, strategy_id: str, name: str, description: str, **changes: object) -> "StrategyConfig":
        return replace(self, strategy_id=strategy_id, name=name, description=description, **changes)

    def includes_result(self, result: object) -> bool:
        return (
            getattr(result, "combined_regime") in self.enabled_regimes
            and getattr(result, "direction") in self.enabled_directions
            and self.capital_score_min <= float(getattr(result, "capital_score")) <= self.capital_score_max
            and float(getattr(result, "space_score")) >= self.space_score_min
        )
