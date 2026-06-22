from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from binance_ai_trader.signals.engine import SignalPolicy


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    minimum_quote_volume_24h: Decimal
    stablecoin_base_assets: frozenset[str]
    leveraged_token_suffixes: tuple[str, ...]
    denied_symbols: frozenset[str]

    @classmethod
    def load(cls, path: Path) -> "UniverseConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            minimum_quote_volume_24h=Decimal(raw["minimum_quote_volume_24h"]),
            stablecoin_base_assets=frozenset(raw["stablecoin_base_assets"]),
            leveraged_token_suffixes=tuple(raw["leveraged_token_suffixes"]),
            denied_symbols=frozenset(raw["denied_symbols"]),
        )


@dataclass(frozen=True, slots=True)
class SectorConfig:
    symbol_to_sector: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> "SectorConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        mappings = raw.get("symbol_to_sector")
        if not isinstance(mappings, dict):
            raise ValueError("symbol_to_sector must be an object")
        return cls(symbol_to_sector={str(symbol): str(sector) for symbol, sector in mappings.items()})


@dataclass(frozen=True)
class StrategyConfig:
    """Live signal generation config loaded from a strategy JSON file.

    All four extra strategies share the same underlying market data
    (klines, scores, capital, space) collected by baseline_v1.  Only the
    signal-selection filters below differ per strategy.
    """

    strategy_id: str
    name: str = ""
    description: str = ""
    entry_distance_min_pct: float = -3.0
    entry_distance_max_pct: float = 1.0
    max_stop_loss_pct: float = 7.0
    min_rr_tp2: float = 2.0
    enabled_regimes: frozenset[str] = field(default_factory=lambda: frozenset({"BULL", "BEAR", "RANGE"}))
    enabled_directions: frozenset[str] = field(default_factory=lambda: frozenset({"LONG", "SHORT"}))
    capital_score_min: float = 0.0
    capital_score_max: float = 100.0
    space_score_min: float = 0.0
    output_limit: int | None = None

    @classmethod
    def from_file(cls, path: Path) -> "StrategyConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            strategy_id=data["strategy_id"],
            name=data.get("name", ""),
            description=data.get("description", ""),
            entry_distance_min_pct=float(data.get("entry_distance_min_pct", -3.0)),
            entry_distance_max_pct=float(data.get("entry_distance_max_pct", 1.0)),
            max_stop_loss_pct=float(data.get("max_stop_loss_pct", 7.0)),
            min_rr_tp2=float(data.get("min_rr_tp2", 2.0)),
            enabled_regimes=frozenset(data.get("enabled_regimes", ["BULL", "BEAR", "RANGE"])),
            enabled_directions=frozenset(data.get("enabled_directions", ["LONG", "SHORT"])),
            capital_score_min=float(data.get("capital_score_min", 0.0)),
            capital_score_max=float(data.get("capital_score_max", 100.0)),
            space_score_min=float(data.get("space_score_min", 0.0)),
            output_limit=data.get("output_limit"),
        )

    def to_signal_policy(self) -> "SignalPolicy":
        from binance_ai_trader.signals.engine import SignalPolicy
        return SignalPolicy(
            entry_distance_min_pct=Decimal(str(self.entry_distance_min_pct)),
            entry_distance_max_pct=Decimal(str(self.entry_distance_max_pct)),
            max_stop_loss_pct=Decimal(str(self.max_stop_loss_pct)),
            min_rr_tp2=Decimal(str(self.min_rr_tp2)),
        )


def load_all_strategy_configs(strategies_dir: Path) -> list[StrategyConfig]:
    """Load every *.json strategy from a directory, sorted by strategy_id.

    Silently skips files that fail to parse so a single bad file
    does not block the entire scan.
    """
    configs: list[StrategyConfig] = []
    for path in sorted(strategies_dir.glob("*.json")):
        try:
            configs.append(StrategyConfig.from_file(path))
        except Exception:
            pass
    return configs
