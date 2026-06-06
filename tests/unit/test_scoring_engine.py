from decimal import Decimal
import unittest

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.scoring import InsufficientDataError, ScoringEngine


def make_klines(symbol: str, interval: str, count: int, growth: float, volume_growth: float = 0.01) -> tuple[Kline, ...]:
    result = []
    price = 100.0
    for index in range(count):
        open_price = price
        close_price = open_price * (1.0 + growth)
        high = max(open_price, close_price) * 1.002
        low = min(open_price, close_price) * 0.998
        quote_volume = 100_000.0 * (1.0 + volume_growth * index)
        result.append(
            Kline(
                symbol=symbol,
                interval=interval,
                open_time_ms=index * 60_000,
                close_time_ms=(index + 1) * 60_000 - 1,
                open=Decimal(str(open_price)),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
                close=Decimal(str(close_price)),
                volume=Decimal("1000"),
                quote_volume=Decimal(str(quote_volume)),
                trade_count=1000 + index * 10,
            )
        )
        price = close_price
    return tuple(result)


def market(symbol: str, growth: float) -> dict[str, tuple[Kline, ...]]:
    return {
        "15m": make_klines(symbol, "15m", 60, growth),
        "1h": make_klines(symbol, "1h", 60, growth),
        "4h": make_klines(symbol, "4h", 60, growth),
    }


class ScoringEngineTest(unittest.TestCase):
    def test_returns_all_five_bounded_components_and_deterministic_total(self) -> None:
        engine = ScoringEngine()
        first = engine.score("BTCUSDT", market("BTCUSDT", 0.005))
        second = engine.score("BTCUSDT", market("BTCUSDT", 0.005))

        self.assertEqual(first, second)
        self.assertEqual({"trend", "volume", "momentum", "structure", "risk"}, set(first.score_breakdown))
        self.assertGreaterEqual(first.score, 0)
        self.assertLessEqual(first.score, 100)
        component_total = sum(item["score"] for item in first.score_breakdown.values())  # type: ignore[index]
        self.assertAlmostEqual(first.score, component_total, places=2)
        self.assertEqual("v1", first.algorithm_version)

    def test_positive_trend_ranks_above_negative_trend(self) -> None:
        engine = ScoringEngine()
        positive = engine.score("UPUSDT", market("UPUSDT", 0.005))
        negative = engine.score("DOWNUSDT", market("DOWNUSDT", -0.005))

        self.assertGreater(positive.score, negative.score)
        self.assertEqual(30.0, positive.score_breakdown["trend"]["score"])  # type: ignore[index]
        self.assertEqual(0, negative.score_breakdown["trend"]["score"])  # type: ignore[index]

    def test_rejects_insufficient_history(self) -> None:
        with self.assertRaisesRegex(InsufficientDataError, "requires 40 closed candles"):
            ScoringEngine().score(
                "BTCUSDT",
                {
                    "15m": make_klines("BTCUSDT", "15m", 39, 0.001),
                    "1h": make_klines("BTCUSDT", "1h", 60, 0.001),
                    "4h": make_klines("BTCUSDT", "4h", 60, 0.001),
                },
            )


if __name__ == "__main__":
    unittest.main()
