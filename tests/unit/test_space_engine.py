from decimal import Decimal
import unittest

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.space import SpaceEngine


def bars() -> tuple[Kline, ...]:
    rows = []
    for index in range(720):
        close = Decimal("100")
        rows.append(Kline("BTCUSDT", "4h", index * 14400000, index * 14400000 + 14399999,
                          close, Decimal("120"), Decimal("80"), close,
                          Decimal("1"), Decimal("100"), 1))
    return tuple(rows)


class SpaceEngineTest(unittest.TestCase):
    def test_calculates_long_and_short_room_without_future_data(self) -> None:
        engine = SpaceEngine()
        long = engine.score("run", "BTCUSDT", "LONG", bars())
        short = engine.score("run", "BTCUSDT", "SHORT", bars())
        self.assertEqual(Decimal("20.00"), long.upside_pct)
        self.assertEqual(Decimal("20.00"), short.downside_pct)
        self.assertEqual(Decimal("100.00"), long.space_score)
        self.assertEqual(Decimal("100.00"), short.space_score)

    def test_requires_full_120_day_window(self) -> None:
        with self.assertRaises(ValueError):
            SpaceEngine().score("run", "BTCUSDT", "LONG", bars()[:-1])
