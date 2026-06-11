from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from binance_ai_trader.domain.models import Kline, SymbolScore, TradeSignal


@dataclass(frozen=True, slots=True)
class SignalPolicy:
    entry_distance_min_pct: Decimal = Decimal("-3")
    entry_distance_max_pct: Decimal = Decimal("1")
    max_stop_loss_pct: Decimal = Decimal("7")
    min_rr_tp2: Decimal = Decimal("2")

    def __post_init__(self) -> None:
        if self.entry_distance_min_pct >= self.entry_distance_max_pct:
            raise ValueError("entry distance minimum must be below maximum")
        if self.max_stop_loss_pct <= 0:
            raise ValueError("maximum stop loss percentage must be positive")
        if self.min_rr_tp2 < 1:
            raise ValueError("minimum TP2 RR must be at least 1")


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    score: SymbolScore
    tick_size: Decimal
    klines: Mapping[str, Sequence[Kline]]


class SignalEngine:
    """Build deterministic, long-only pullback signals from ranked symbols."""

    kline_limits = {"15m": 80, "1h": 80, "4h": 80}
    minimum_klines = {"15m": 25, "1h": 25, "4h": 25}

    def __init__(self, policy: SignalPolicy | None = None) -> None:
        self.policy = policy or SignalPolicy()

    def generate(self, candidate: SignalCandidate) -> TradeSignal | None:
        self._validate(candidate)
        fifteen_minute = candidate.klines["15m"]
        hourly = candidate.klines["1h"]
        four_hourly = candidate.klines["4h"]
        latest_close = fifteen_minute[-1].close
        atr = _atr(hourly, 14)

        support = self._entry_support(
            fifteen_minute, hourly, latest_close,
            self.policy.entry_distance_min_pct, self.policy.entry_distance_max_pct,
        )
        if support is None:
            return None
        support_price, support_interval = support
        entry = _round_nearest(support_price * Decimal("1.001"), candidate.tick_size)
        entry_distance_pct = _percent(entry - latest_close, latest_close)
        if (
            entry_distance_pct < self.policy.entry_distance_min_pct
            or entry_distance_pct > self.policy.entry_distance_max_pct
        ):
            return None

        stop_result = self._stop_loss(hourly, entry, atr, candidate.tick_size)
        if stop_result is None:
            return None
        stop_loss, stop_method = stop_result
        risk = entry - stop_loss
        stop_loss_pct = _percent(risk, entry)
        if stop_loss >= entry or stop_loss_pct > self.policy.max_stop_loss_pct:
            return None

        targets = self._targets(
            hourly, four_hourly, entry, risk, candidate.tick_size, self.policy.min_rr_tp2
        )
        if targets is None:
            return None
        tp1, tp2, tp1_source, tp2_source = targets
        rr_tp1 = (tp1 - entry) / risk
        rr_tp2 = (tp2 - entry) / risk
        if tp1 <= entry or tp2 <= tp1 or rr_tp1 < Decimal("1") or rr_tp2 < self.policy.min_rr_tp2:
            return None

        logic = (
            f"LONG pullback: entry near {support_interval} support; "
            f"stop uses {stop_method} ({_display(stop_loss_pct)}% risk); "
            f"TP1 uses {tp1_source} at {_display(rr_tp1)}R and "
            f"TP2 uses {tp2_source} at {_display(rr_tp2)}R."
        )
        return TradeSignal(
            symbol=candidate.score.symbol,
            direction="LONG",
            score=candidate.score.score,
            entry=entry,
            latest_close=latest_close,
            stop_loss=stop_loss,
            stop_loss_pct=_two_places(stop_loss_pct),
            tp1=tp1,
            tp2=tp2,
            rr_tp1=_two_places(rr_tp1),
            rr_tp2=_two_places(rr_tp2),
            logic_summary=logic,
        )

    def _validate(self, candidate: SignalCandidate) -> None:
        if candidate.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        for interval, minimum in self.minimum_klines.items():
            items = candidate.klines.get(interval, ())
            if len(items) < minimum:
                raise ValueError(f"{candidate.score.symbol}/{interval} requires at least {minimum} candles")
            if any(item.symbol != candidate.score.symbol or item.interval != interval for item in items):
                raise ValueError(f"{candidate.score.symbol}/{interval} contains mismatched kline data")
            if any(current.open_time_ms <= previous.open_time_ms for previous, current in zip(items, items[1:])):
                raise ValueError(f"{candidate.score.symbol}/{interval} klines must be chronological")

    @staticmethod
    def _entry_support(
        fifteen_minute: Sequence[Kline], hourly: Sequence[Kline], latest_close: Decimal,
        minimum_distance_pct: Decimal, maximum_distance_pct: Decimal,
    ) -> tuple[Decimal, str] | None:
        candidates = [
            *((price, "15m swing low") for price in _pivot_lows(fifteen_minute[-50:])),
            *((price, "1h swing low") for price in _pivot_lows(hourly[-40:])),
        ]
        eligible = [
            item
            for item in candidates
            if minimum_distance_pct
            <= _percent(item[0] - latest_close, latest_close)
            <= maximum_distance_pct
        ]
        if not eligible:
            return None
        return min(eligible, key=lambda item: (abs(item[0] - latest_close), 0 if item[1].startswith("15m") else 1))

    @staticmethod
    def _stop_loss(
        hourly: Sequence[Kline], entry: Decimal, atr: Decimal, tick_size: Decimal
    ) -> tuple[Decimal, str] | None:
        swing_lows = [price for price in _pivot_lows(hourly[-50:]) if price < entry]
        atr_stop = entry - atr * Decimal("2")
        if swing_lows:
            structural_stop = swing_lows[-1] - atr * Decimal("0.25")
            raw_stop = max(structural_stop, atr_stop)
            method = "1h swing low/ATR buffer"
        else:
            raw_stop = atr_stop
            method = "1h ATR"

        maximum_stop_for_minimum_risk = entry * Decimal("0.98")
        raw_stop = min(raw_stop, maximum_stop_for_minimum_risk)
        stop_loss = _round_down(raw_stop, tick_size)
        if stop_loss <= 0 or stop_loss >= entry:
            return None
        return stop_loss, method

    @staticmethod
    def _targets(
        hourly: Sequence[Kline],
        four_hourly: Sequence[Kline],
        entry: Decimal,
        risk: Decimal,
        tick_size: Decimal,
        minimum_rr_tp2: Decimal,
    ) -> tuple[Decimal, Decimal, str, str] | None:
        resistance = [
            *((price, "1h prior high") for price in _pivot_highs(hourly[-60:])),
            *((price, "4h prior high") for price in _pivot_highs(four_hourly[-60:])),
            (max(item.high for item in hourly[-25:-1]), "1h range high"),
            (max(item.high for item in four_hourly[-25:-1]), "4h range high"),
        ]
        resistance = sorted(
            {(price, source) for price, source in resistance if price > entry},
            key=lambda item: (item[0], item[1]),
        )
        minimum_tp2 = entry + risk * minimum_rr_tp2
        tp2_candidates = [item for item in resistance if item[0] >= minimum_tp2]
        if not tp2_candidates:
            return None
        raw_tp2, tp2_source = tp2_candidates[0]
        minimum_tp1 = entry + risk
        tp1_candidates = [item for item in resistance if minimum_tp1 <= item[0] < raw_tp2]
        if tp1_candidates:
            raw_tp1, tp1_source = tp1_candidates[0]
        else:
            raw_tp1, tp1_source = minimum_tp1, "1R objective"

        tp1 = _round_up(raw_tp1, tick_size)
        tp2 = _round_up(raw_tp2, tick_size)
        return tp1, tp2, tp1_source, tp2_source


def _pivot_lows(klines: Sequence[Kline], width: int = 2) -> list[Decimal]:
    result: list[Decimal] = []
    for index in range(width, len(klines) - width):
        value = klines[index].low
        neighbors = [
            item.low
            for item in (*klines[index - width:index], *klines[index + 1:index + width + 1])
        ]
        if value < min(neighbors):
            result.append(value)
    return result


def _pivot_highs(klines: Sequence[Kline], width: int = 2) -> list[Decimal]:
    result: list[Decimal] = []
    for index in range(width, len(klines) - width):
        value = klines[index].high
        neighbors = [
            item.high
            for item in (*klines[index - width:index], *klines[index + 1:index + width + 1])
        ]
        if value > max(neighbors):
            result.append(value)
    return result


def _atr(klines: Sequence[Kline], period: int) -> Decimal:
    ranges: list[Decimal] = []
    previous_close = klines[-period - 1].close
    for item in klines[-period:]:
        ranges.append(max(item.high - item.low, abs(item.high - previous_close), abs(item.low - previous_close)))
        previous_close = item.close
    return sum(ranges, Decimal("0")) / Decimal(period)


def _percent(value: Decimal, base: Decimal) -> Decimal:
    return value / base * Decimal("100")


def _round_nearest(value: Decimal, tick_size: Decimal) -> Decimal:
    return (value / tick_size).to_integral_value(rounding=ROUND_HALF_UP) * tick_size


def _round_down(value: Decimal, tick_size: Decimal) -> Decimal:
    return (value / tick_size).to_integral_value(rounding=ROUND_FLOOR) * tick_size


def _round_up(value: Decimal, tick_size: Decimal) -> Decimal:
    return (value / tick_size).to_integral_value(rounding=ROUND_CEILING) * tick_size


def _two_places(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _display(value: Decimal) -> str:
    return format(_two_places(value), "f")
