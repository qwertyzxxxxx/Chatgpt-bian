from decimal import Decimal
import unittest

from binance_ai_trader.domain.models import Kline, SymbolScore
from binance_ai_trader.signals import SignalCandidate, SignalEngine


def signal_klines(
    symbol: str,
    support: Decimal = Decimal("98"),
    hourly_high: Decimal = Decimal("103"),
    four_hour_high: Decimal = Decimal("106"),
    wide_hourly_range: bool = False,
) -> dict[str, tuple[Kline, ...]]:
    return {
        "15m": _series(symbol, "15m", support=support, prior_high=Decimal("101")),
        "1h": _series(
            symbol,
            "1h",
            support=support,
            prior_high=hourly_high,
            half_range=Decimal("8") if wide_hourly_range else Decimal("0.5"),
        ),
        "4h": _series(symbol, "4h", support=Decimal("95"), prior_high=four_hour_high),
    }


def _series(
    symbol: str,
    interval: str,
    support: Decimal,
    prior_high: Decimal,
    half_range: Decimal = Decimal("0.5"),
) -> tuple[Kline, ...]:
    result = []
    for index in range(80):
        low = Decimal("100") - half_range
        high = Decimal("100") + half_range
        if index == 60:
            high = prior_high
        if index == 75:
            low = support
        result.append(
            Kline(
                symbol=symbol,
                interval=interval,
                open_time_ms=index * 60_000,
                close_time_ms=(index + 1) * 60_000 - 1,
                open=Decimal("100"),
                high=high,
                low=low,
                close=Decimal("100"),
                volume=Decimal("1000"),
                quote_volume=Decimal("100000"),
                trade_count=1000,
            )
        )
    return tuple(result)


def candidate(symbol: str = "BTCUSDT", **kwargs: object) -> SignalCandidate:
    return SignalCandidate(
        score=SymbolScore(symbol, 88.5, {}, "v1"),
        tick_size=Decimal("0.01"),
        klines=signal_klines(symbol, **kwargs),  # type: ignore[arg-type]
    )


class SignalEngineTest(unittest.TestCase):
    def test_generates_valid_deterministic_long_signal(self) -> None:
        engine = SignalEngine()
        first = engine.generate(candidate())
        second = engine.generate(candidate())

        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual("LONG", first.direction)
        self.assertLessEqual(Decimal("-3"), (first.entry - first.latest_close) / first.latest_close * 100)
        self.assertLessEqual((first.entry - first.latest_close) / first.latest_close * 100, Decimal("1"))
        self.assertLess(first.stop_loss, first.entry)
        self.assertGreaterEqual(first.stop_loss_pct, Decimal("2"))
        self.assertLessEqual(first.stop_loss_pct, Decimal("7"))
        self.assertGreaterEqual(first.rr_tp1, Decimal("1"))
        self.assertGreaterEqual(first.rr_tp2, Decimal("2"))
        self.assertLess(first.entry, first.tp1)
        self.assertLess(first.tp1, first.tp2)

    def test_skips_when_pullback_support_is_more_than_three_percent_away(self) -> None:
        self.assertIsNone(SignalEngine().generate(candidate(support=Decimal("90"))))

    def test_skips_when_stop_risk_exceeds_seven_percent(self) -> None:
        self.assertIsNone(SignalEngine().generate(candidate(wide_hourly_range=True)))

    def test_skips_when_no_structural_target_reaches_two_r(self) -> None:
        self.assertIsNone(
            SignalEngine().generate(
                candidate(hourly_high=Decimal("101"), four_hour_high=Decimal("101.5"))
            )
        )


if __name__ == "__main__":
    unittest.main()
