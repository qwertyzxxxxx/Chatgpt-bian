from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


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
