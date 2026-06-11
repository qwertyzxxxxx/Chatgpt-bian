from decimal import Decimal
import unittest

from binance_ai_trader.domain.models import Kline, SymbolScore
from binance_ai_trader.signals import ShortSignalEngine, SignalCandidate


def short_klines(
    symbol: str,
    resistance: Decimal = Decimal("102"),
    hourly_low: Decimal = Decimal("97"),
    four_hour_low: Decimal = Decimal("94"),
    wide_hourly_range: bool = False,
) -> dict[str, tuple[Kline, ...]]:
    return {
        "15m": _series(symbol, "15m", resistance, Decimal("98")),
        "1h": _series(
            symbol, "1h", resistance, hourly_low,
            Decimal("8") if wide_hourly_range else Decimal("0.5"),
        ),
        "4h": _series(symbol, "4h", Decimal("105"), four_hour_low),
    }


def _series(
    symbol: str,
    interval: str,
    resistance: Decimal,
    prior_low: Decimal,
    half_range: Decimal = Decimal("0.5"),
) -> tuple[Kline, ...]:
    result = []
    for index in range(80):
        high = Decimal("100") + half_range
        low = Decimal("100") - half_range
        if index == 60:
            low = prior_low
        if index == 75:
            high = resistance
        result.append(
            Kline(
                symbol=symbol, interval=interval,
                open_time_ms=index * 60_000, close_time_ms=(index + 1) * 60_000 - 1,
                open=Decimal("100"), high=high, low=low, close=Decimal("100"),
                volume=Decimal("1000"), quote_volume=Decimal("100000"), trade_count=1000,
            )
        )
    return tuple(result)


def short_candidate(symbol: str = "WEAKUSDT", **kwargs: object) -> SignalCandidate:
    return SignalCandidate(
        score=SymbolScore(symbol, 90, {}, "v1"),
        tick_size=Decimal("0.01"),
        klines=short_klines(symbol, **kwargs),  # type: ignore[arg-type]
    )


class ShortSignalEngineTest(unittest.TestCase):
    def test_generates_valid_deterministic_short_signal(self) -> None:
        engine = ShortSignalEngine()
        first = engine.generate(short_candidate())
        second = engine.generate(short_candidate())
        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual("SHORT", first.direction)
        self.assertGreater(first.entry, first.latest_close)
        self.assertGreater(first.stop_loss, first.entry)
        self.assertGreaterEqual(first.stop_loss_pct, Decimal("2"))
        self.assertLessEqual(first.stop_loss_pct, Decimal("7"))
        self.assertGreater(first.entry, first.tp1)
        self.assertGreater(first.tp1, first.tp2)
        self.assertGreaterEqual(first.rr_tp1, Decimal("1"))
        self.assertGreaterEqual(first.rr_tp2, Decimal("2"))

    def test_skips_when_resistance_is_more_than_three_percent_away(self) -> None:
        self.assertIsNone(ShortSignalEngine().generate(short_candidate(resistance=Decimal("106"))))

    def test_skips_when_stop_risk_exceeds_seven_percent(self) -> None:
        self.assertIsNone(ShortSignalEngine().generate(short_candidate(wide_hourly_range=True)))

    def test_skips_without_structural_room_for_two_r(self) -> None:
        self.assertIsNone(
            ShortSignalEngine().generate(
                short_candidate(hourly_low=Decimal("99"), four_hour_low=Decimal("98"))
            )
        )


if __name__ == "__main__":
    unittest.main()
