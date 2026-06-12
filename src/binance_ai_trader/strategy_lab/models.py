from __future__ import annotations

from dataclasses import dataclass

from binance_ai_trader.domain.models import BacktestMetrics
from binance_ai_trader.strategy_lab.config import StrategyConfig


@dataclass(frozen=True, slots=True)
class StrategyVersion:
    strategy_id: str
    name: str
    description: str
    config: StrategyConfig
    status: str
    created_at: str
    metrics: BacktestMetrics | None


@dataclass(frozen=True, slots=True)
class StrategyComparison:
    strategy_id: str
    metrics: BacktestMetrics
    regime_breakdown: dict[str, BacktestMetrics]
