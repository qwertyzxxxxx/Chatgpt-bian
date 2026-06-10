from __future__ import annotations

from typing import Protocol

from binance_ai_trader.capital.engine import CapitalFlowEngine, CapitalInputs, CapitalSnapshot


class CapitalInputsReader(Protocol):
    def load_capital_inputs_at(self, symbol: str, as_of_ms: int) -> CapitalInputs | None: ...


class CapitalFlowHistory:
    """Reconstruct Capital Flow through one point-in-time path for live and backtest."""

    def __init__(
        self,
        repository: CapitalInputsReader,
        engine: CapitalFlowEngine | None = None,
    ) -> None:
        self._repository = repository
        self._engine = engine or CapitalFlowEngine()

    def score_at(
        self, run_id: str, symbol: str, as_of_ms: int
    ) -> CapitalSnapshot | None:
        inputs = self._repository.load_capital_inputs_at(symbol, as_of_ms)
        return None if inputs is None else self._engine.score(run_id, inputs)
