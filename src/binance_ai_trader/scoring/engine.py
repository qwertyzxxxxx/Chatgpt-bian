from __future__ import annotations

from collections.abc import Mapping, Sequence

from binance_ai_trader.domain.models import Kline, SymbolScore
from binance_ai_trader.scoring.momentum_score import MomentumScore
from binance_ai_trader.scoring.risk_score import RiskScore
from binance_ai_trader.scoring.structure_score import StructureScore
from binance_ai_trader.scoring.trend_score import TrendScore
from binance_ai_trader.scoring.volume_score import VolumeScore


class InsufficientDataError(ValueError):
    pass


class ScoringEngine:
    algorithm_version = "v1"
    minimum_candles = {"15m": 40, "1h": 55, "4h": 55}

    default_weights = {"trend": 30.0, "volume": 20.0, "momentum": 20.0, "structure": 15.0, "risk": 15.0}

    def __init__(self, weights: Mapping[str, float] | None = None) -> None:
        self._weights = dict(weights or self.default_weights)
        if set(self._weights) != set(self.default_weights):
            raise ValueError("scoring weights must define trend, volume, momentum, structure, and risk")
        if any(value < 0 for value in self._weights.values()) or round(sum(self._weights.values()), 8) != 100:
            raise ValueError("scoring weights must be non-negative and total 100")
        self._trend = TrendScore()
        self._volume = VolumeScore()
        self._momentum = MomentumScore()
        self._structure = StructureScore()
        self._risk = RiskScore()

    def score(self, symbol: str, klines: Mapping[str, Sequence[Kline]]) -> SymbolScore:
        self._validate(symbol, klines)
        fifteen_minute = klines["15m"]
        hourly = klines["1h"]
        four_hourly = klines["4h"]
        components = {
            "trend": self._trend.calculate(hourly, four_hourly),
            "volume": self._volume.calculate(fifteen_minute, hourly),
            "momentum": self._momentum.calculate(hourly, four_hourly),
            "structure": self._structure.calculate(fifteen_minute, hourly),
            "risk": self._risk.calculate(fifteen_minute, hourly),
        }
        total = round(
            sum(
                round(component.score, 2) / component.maximum * self._weights[name]
                for name, component in components.items()
            ),
            2,
        )
        breakdown = {name: component.as_dict() for name, component in components.items()}
        return SymbolScore(symbol=symbol, score=total, score_breakdown=breakdown, algorithm_version=self.algorithm_version)

    def _validate(self, symbol: str, klines: Mapping[str, Sequence[Kline]]) -> None:
        for interval, minimum in self.minimum_candles.items():
            items = klines.get(interval, ())
            if len(items) < minimum:
                raise InsufficientDataError(f"{symbol}/{interval} requires {minimum} closed candles, got {len(items)}")
            if any(item.symbol != symbol or item.interval != interval for item in items):
                raise ValueError(f"{symbol}/{interval} contains mismatched kline data")
            if any(current.open_time_ms <= previous.open_time_ms for previous, current in zip(items, items[1:])):
                raise ValueError(f"{symbol}/{interval} klines must be strictly chronological")
