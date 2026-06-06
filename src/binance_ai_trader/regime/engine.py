from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from binance_ai_trader.domain.models import Kline, MarketRegime


class RegimeState(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"
    RANGE = "RANGE"
    OBSERVE = "OBSERVE"


@dataclass(frozen=True, slots=True)
class RegimePolicy:
    minimum_candles: int = 51
    atr_period: int = 14
    maximum_atr_pct_1h: Decimal = Decimal("5")
    maximum_atr_pct_4h: Decimal = Decimal("8")


class MarketRegimeEngine:
    intervals = ("15m", "1h", "4h")

    def __init__(self, policy: RegimePolicy | None = None) -> None:
        self.policy = policy or RegimePolicy()

    def evaluate(
        self,
        btc_klines: Mapping[str, Sequence[Kline]],
        eth_klines: Mapping[str, Sequence[Kline]],
    ) -> MarketRegime:
        btc = self.evaluate_asset("BTCUSDT", btc_klines)
        eth = self.evaluate_asset("ETHUSDT", eth_klines)
        return MarketRegime(
            btc_regime=btc.value,
            eth_regime=eth.value,
            combined_regime=self.combine(btc, eth).value,
        )

    def evaluate_asset(
        self,
        symbol: str,
        klines: Mapping[str, Sequence[Kline]],
    ) -> RegimeState:
        if not self._is_valid(symbol, klines):
            return RegimeState.OBSERVE

        fifteen = klines["15m"]
        hourly = klines["1h"]
        four_hourly = klines["4h"]
        hourly_atr_pct = _atr_pct(hourly, self.policy.atr_period)
        four_hour_atr_pct = _atr_pct(four_hourly, self.policy.atr_period)
        if (
            hourly_atr_pct > self.policy.maximum_atr_pct_1h
            or four_hour_atr_pct > self.policy.maximum_atr_pct_4h
        ):
            return RegimeState.OBSERVE

        fifteen_trend = _trend(fifteen)
        hourly_trend = _trend(hourly)
        four_hour_trend = _trend(four_hourly)
        if hourly_trend == RegimeState.BULL and four_hour_trend == RegimeState.BULL:
            return RegimeState.BULL if fifteen_trend != RegimeState.BEAR else RegimeState.OBSERVE
        if hourly_trend == RegimeState.BEAR and four_hour_trend == RegimeState.BEAR:
            return RegimeState.BEAR if fifteen_trend != RegimeState.BULL else RegimeState.OBSERVE
        if {hourly_trend, four_hour_trend} == {RegimeState.BULL, RegimeState.BEAR}:
            return RegimeState.OBSERVE
        return RegimeState.RANGE

    @staticmethod
    def combine(btc: RegimeState, eth: RegimeState) -> RegimeState:
        if btc == eth and btc in {RegimeState.BULL, RegimeState.BEAR}:
            return btc
        if RegimeState.OBSERVE in {btc, eth}:
            return RegimeState.OBSERVE
        if {btc, eth} == {RegimeState.BULL, RegimeState.BEAR}:
            return RegimeState.OBSERVE
        return RegimeState.RANGE

    def _is_valid(self, symbol: str, klines: Mapping[str, Sequence[Kline]]) -> bool:
        for interval in self.intervals:
            items = klines.get(interval, ())
            if len(items) < self.policy.minimum_candles:
                return False
            if any(item.symbol != symbol or item.interval != interval for item in items):
                return False
            if any(
                current.open_time_ms <= previous.open_time_ms
                for previous, current in zip(items, items[1:])
            ):
                return False
            if any(item.close <= 0 or item.high < item.low for item in items):
                return False
        return True


def _trend(klines: Sequence[Kline]) -> RegimeState:
    closes = [item.close for item in klines]
    latest = closes[-1]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    if latest > ema20 > ema50:
        return RegimeState.BULL
    if latest < ema20 < ema50:
        return RegimeState.BEAR
    return RegimeState.RANGE


def _ema(values: Sequence[Decimal], period: int) -> Decimal:
    alpha = Decimal("2") / Decimal(period + 1)
    result = sum(values[:period], Decimal("0")) / Decimal(period)
    for value in values[period:]:
        result = alpha * value + (Decimal("1") - alpha) * result
    return result


def _atr_pct(klines: Sequence[Kline], period: int) -> Decimal:
    selected = klines[-period:]
    previous_close = klines[-period - 1].close
    ranges: list[Decimal] = []
    for item in selected:
        ranges.append(
            max(item.high - item.low, abs(item.high - previous_close), abs(item.low - previous_close))
        )
        previous_close = item.close
    atr = sum(ranges, Decimal("0")) / Decimal(period)
    return atr / selected[-1].close * Decimal("100")
