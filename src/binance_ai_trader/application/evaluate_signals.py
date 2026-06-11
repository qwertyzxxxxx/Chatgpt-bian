from __future__ import annotations

from datetime import UTC, datetime

from binance_ai_trader.domain.models import EvaluationSummary
from binance_ai_trader.evaluation import SignalEvaluationEngine
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


class SignalEvaluator:
    def __init__(
        self,
        repository: MarketDataRepository,
        engine: SignalEvaluationEngine | None = None,
    ) -> None:
        self._repository = repository
        self._engine = engine or SignalEvaluationEngine()

    def evaluate_all(self) -> EvaluationSummary:
        evaluations = []
        for signal in self._repository.load_signals_for_evaluation():
            bars = self._repository.load_future_klines(
                symbol=signal.symbol,
                interval="15m",
                after_close_time_ms=signal.generated_at_ms,
                limit=self._engine.policy.maximum_bars,
            )
            evaluation = self._engine.evaluate(signal, bars)
            if evaluation is not None:
                evaluations.append(evaluation)
        self._repository.save_signal_evaluations(evaluations, _utc_now())
        return self._repository.load_evaluation_summary()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
