from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from binance_ai_trader.domain.models import Kline, SignalEvaluation, StoredSignal


@dataclass(frozen=True, slots=True)
class EvaluationPolicy:
    maximum_bars: int = 96

    def __post_init__(self) -> None:
        if self.maximum_bars < 1:
            raise ValueError("maximum_bars must be positive")


class SignalEvaluationEngine:
    """Evaluate deterministic LONG and SHORT signals on closed future 15m bars."""

    def __init__(self, policy: EvaluationPolicy | None = None) -> None:
        self.policy = policy or EvaluationPolicy()

    def evaluate(self, signal: StoredSignal, bars: Sequence[Kline]) -> SignalEvaluation | None:
        self._validate(signal, bars)
        future_bars = tuple(bars[: self.policy.maximum_bars])
        activated = False
        tp1_bar: int | None = None
        maximum_favorable = Decimal("0")
        maximum_adverse = Decimal("0")

        for bar_number, bar in enumerate(future_bars, start=1):
            if not activated:
                activated = bar.low <= signal.entry <= bar.high
                if not activated:
                    continue

            favorable, adverse, stop_hit, tp1_hit, tp2_hit = self._bar_state(signal, bar)
            maximum_favorable = max(maximum_favorable, favorable)
            maximum_adverse = max(maximum_adverse, adverse)

            # Conservative intrabar assumption: a stop wins every target/stop tie.
            if stop_hit:
                return self._result(signal, "LOSS", maximum_favorable, maximum_adverse, bar_number)
            if tp2_hit:
                return self._result(signal, "WIN_TP2", maximum_favorable, maximum_adverse, bar_number)
            if tp1_hit and tp1_bar is None:
                tp1_bar = bar_number

        if len(future_bars) < self.policy.maximum_bars:
            return None
        if tp1_bar is not None:
            return self._result(signal, "TP1_HIT", maximum_favorable, maximum_adverse, tp1_bar)
        return self._result(signal, "EXPIRED", maximum_favorable, maximum_adverse, self.policy.maximum_bars)

    @staticmethod
    def _bar_state(
        signal: StoredSignal, bar: Kline
    ) -> tuple[Decimal, Decimal, bool, bool, bool]:
        if signal.direction == "LONG":
            favorable = _percentage(max(bar.high - signal.entry, Decimal("0")), signal.entry)
            adverse = _percentage(max(signal.entry - bar.low, Decimal("0")), signal.entry)
            return favorable, adverse, bar.low <= signal.stop_loss, bar.high >= signal.tp1, bar.high >= signal.tp2
        favorable = _percentage(max(signal.entry - bar.low, Decimal("0")), signal.entry)
        adverse = _percentage(max(bar.high - signal.entry, Decimal("0")), signal.entry)
        return favorable, adverse, bar.high >= signal.stop_loss, bar.low <= signal.tp1, bar.low <= signal.tp2

    @staticmethod
    def _validate(signal: StoredSignal, bars: Sequence[Kline]) -> None:
        if signal.direction == "LONG":
            valid_order = signal.stop_loss < signal.entry < signal.tp1 < signal.tp2
        elif signal.direction == "SHORT":
            valid_order = signal.tp2 < signal.tp1 < signal.entry < signal.stop_loss
        else:
            raise ValueError("direction must be LONG or SHORT")
        if not valid_order:
            raise ValueError(f"invalid {signal.direction} signal price ordering")
        if any(bar.symbol != signal.symbol or bar.interval != "15m" for bar in bars):
            raise ValueError("evaluation bars must be 15m bars for the signal symbol")
        if any(current.open_time_ms <= previous.open_time_ms for previous, current in zip(bars, bars[1:])):
            raise ValueError("evaluation bars must be chronological")
        if any(bar.close_time_ms <= signal.generated_at_ms for bar in bars):
            raise ValueError("evaluation bars must close after signal generation")

    @staticmethod
    def _result(
        signal: StoredSignal,
        result: str,
        maximum_favorable: Decimal,
        maximum_adverse: Decimal,
        bars_to_result: int,
    ) -> SignalEvaluation:
        return SignalEvaluation(
            signal_run_id=signal.run_id,
            symbol=signal.symbol,
            direction=signal.direction,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            result=result,
            max_favorable_pct=_two_places(maximum_favorable),
            max_adverse_pct=_two_places(maximum_adverse),
            bars_to_result=bars_to_result,
            snapshot_id=signal.snapshot_id,
        )


def _percentage(value: Decimal, base: Decimal) -> Decimal:
    return value / base * Decimal("100")


def _two_places(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
