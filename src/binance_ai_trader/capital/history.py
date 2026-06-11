from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from binance_ai_trader.capital.engine import CapitalFlowEngine, CapitalInputs, CapitalSnapshot


class CapitalInputsReader(Protocol):
    def load_capital_inputs_at(self, symbol: str, as_of_ms: int) -> CapitalInputs | None: ...
    def load_capital_input_quality_at(self, symbol: str, as_of_ms: int) -> str: ...


@dataclass(frozen=True, slots=True)
class CapitalFlowAssessment:
    snapshot: CapitalSnapshot | None
    data_quality_status: str


class CapitalFlowHistory:
    """Reconstruct Capital Flow through one point-in-time path for live and backtest."""

    def __init__(
        self,
        repository: CapitalInputsReader,
        engine: CapitalFlowEngine | None = None,
    ) -> None:
        self._repository = repository
        self._engine = engine or CapitalFlowEngine()

    def assess_at(
        self, run_id: str, symbol: str, as_of_ms: int
    ) -> CapitalFlowAssessment:
        quality = self._repository.load_capital_input_quality_at(symbol, as_of_ms)
        inputs = self._repository.load_capital_inputs_at(symbol, as_of_ms)
        snapshot = None if inputs is None else self._engine.score(run_id, inputs)
        if snapshot is None and quality == "COMPLETE":
            quality = "PARTIAL"
        return CapitalFlowAssessment(snapshot, quality)

    def score_at(
        self, run_id: str, symbol: str, as_of_ms: int
    ) -> CapitalSnapshot | None:
        return self.assess_at(run_id, symbol, as_of_ms).snapshot
