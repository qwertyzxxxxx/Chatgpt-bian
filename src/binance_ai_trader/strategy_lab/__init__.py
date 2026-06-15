from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.strategy_lab.models import (
    ChampionStanding,
    StrategyComparison,
    StrategyRanking,
    StrategySweepResult,
    StrategyVersion,
)

__all__ = [
    "StrategyComparison",
    "ChampionStanding",
    "StrategyConfig",
    "StrategyLab",
    "StrategyRanking",
    "StrategySweepResult",
    "StrategyVersion",
]


def __getattr__(name: str):
    if name == "StrategyLab":
        from binance_ai_trader.strategy_lab.service import StrategyLab

        return StrategyLab
    raise AttributeError(name)
