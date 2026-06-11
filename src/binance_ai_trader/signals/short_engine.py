from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from binance_ai_trader.domain.models import Kline, TradeSignal
from binance_ai_trader.signals.engine import (
    SignalCandidate,
    SignalEngine,
    SignalPolicy,
    _atr,
    _display,
    _percent,
    _pivot_highs,
    _pivot_lows,
    _round_down,
    _round_nearest,
    _round_up,
    _two_places,
)


class ShortSignalEngine(SignalEngine):
    """Build deterministic SHORT rebound signals without placing orders."""

    def __init__(self, policy: SignalPolicy | None = None) -> None:
        super().__init__(policy)

    def generate(self, candidate: SignalCandidate) -> TradeSignal | None:
        self._validate(candidate)
        fifteen_minute = candidate.klines["15m"]
        hourly = candidate.klines["1h"]
        four_hourly = candidate.klines["4h"]
        latest_close = fifteen_minute[-1].close
        atr = _atr(hourly, 14)

        resistance = self._entry_resistance(fifteen_minute, hourly, latest_close)
        if resistance is None:
            return None
        resistance_price, resistance_interval = resistance
        entry = _round_nearest(resistance_price * Decimal("0.999"), candidate.tick_size)
        entry_distance_pct = _percent(entry - latest_close, latest_close)
        if entry_distance_pct < Decimal("-1") or entry_distance_pct > Decimal("3"):
            return None

        stop_result = self._stop_loss(hourly, entry, atr, candidate.tick_size)
        if stop_result is None:
            return None
        stop_loss, stop_method = stop_result
        risk = stop_loss - entry
        stop_loss_pct = _percent(risk, entry)
        if stop_loss <= entry or stop_loss_pct > self.policy.max_stop_loss_pct:
            return None

        targets = self._targets(
            hourly, four_hourly, entry, risk, candidate.tick_size, self.policy.min_rr_tp2
        )
        if targets is None:
            return None
        tp1, tp2, tp1_source, tp2_source = targets
        rr_tp1 = (entry - tp1) / risk
        rr_tp2 = (entry - tp2) / risk
        if tp2 >= tp1 or tp1 >= entry or rr_tp1 < Decimal("1") or rr_tp2 < self.policy.min_rr_tp2:
            return None

        logic = (
            f"SHORT rebound: entry near {resistance_interval} resistance; "
            f"stop uses {stop_method} ({_display(stop_loss_pct)}% risk); "
            f"TP1 uses {tp1_source} at {_display(rr_tp1)}R and "
            f"TP2 uses {tp2_source} at {_display(rr_tp2)}R."
        )
        return TradeSignal(
            symbol=candidate.score.symbol,
            direction="SHORT",
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

    @staticmethod
    def _entry_resistance(
        fifteen_minute: Sequence[Kline], hourly: Sequence[Kline], latest_close: Decimal
    ) -> tuple[Decimal, str] | None:
        candidates = [
            *((price, "15m swing high") for price in _pivot_highs(fifteen_minute[-50:])),
            *((price, "1h swing high") for price in _pivot_highs(hourly[-40:])),
        ]
        eligible = [
            item
            for item in candidates
            if Decimal("-1") <= _percent(item[0] - latest_close, latest_close) <= Decimal("3")
        ]
        if not eligible:
            return None
        return min(
            eligible,
            key=lambda item: (
                abs(item[0] - latest_close),
                0 if item[1].startswith("15m") else 1,
            ),
        )

    @staticmethod
    def _stop_loss(
        hourly: Sequence[Kline], entry: Decimal, atr: Decimal, tick_size: Decimal
    ) -> tuple[Decimal, str] | None:
        swing_highs = [price for price in _pivot_highs(hourly[-50:]) if price > entry]
        atr_stop = entry + atr * Decimal("2")
        if swing_highs:
            structural_stop = swing_highs[-1] + atr * Decimal("0.25")
            raw_stop = min(structural_stop, atr_stop)
            method = "1h swing high/ATR buffer"
        else:
            raw_stop = atr_stop
            method = "1h ATR"
        raw_stop = max(raw_stop, entry * Decimal("1.02"))
        stop_loss = _round_up(raw_stop, tick_size)
        if stop_loss <= entry:
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
        support = [
            *((price, "1h prior low") for price in _pivot_lows(hourly[-60:])),
            *((price, "4h prior low") for price in _pivot_lows(four_hourly[-60:])),
            (min(item.low for item in hourly[-25:-1]), "1h range low"),
            (min(item.low for item in four_hourly[-25:-1]), "4h range low"),
        ]
        support = sorted(
            {(price, source) for price, source in support if price < entry},
            key=lambda item: (-item[0], item[1]),
        )
        maximum_tp2 = entry - risk * minimum_rr_tp2
        tp2_candidates = [item for item in support if item[0] <= maximum_tp2]
        if not tp2_candidates:
            return None
        raw_tp2, tp2_source = tp2_candidates[0]
        maximum_tp1 = entry - risk
        tp1_candidates = [item for item in support if raw_tp2 < item[0] <= maximum_tp1]
        if tp1_candidates:
            raw_tp1, tp1_source = tp1_candidates[0]
        else:
            raw_tp1, tp1_source = maximum_tp1, "1R objective"

        tp1 = _round_down(raw_tp1, tick_size)
        tp2 = _round_down(raw_tp2, tick_size)
        return tp1, tp2, tp1_source, tp2_source
