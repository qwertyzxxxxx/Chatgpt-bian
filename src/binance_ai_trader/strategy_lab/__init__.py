from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.strategy_lab.models import StrategyComparison, StrategyVersion

__all__ = ["StrategyComparison", "StrategyConfig", "StrategyLab", "StrategyVersion"]


def __getattr__(name: str):
    if name == "StrategyLab":
        from binance_ai_trader.strategy_lab.service import StrategyLab

        return StrategyLab
    raise AttributeError(name)
