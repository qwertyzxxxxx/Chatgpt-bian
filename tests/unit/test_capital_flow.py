from decimal import Decimal
import unittest

from binance_ai_trader.capital import CapitalFlowEngine, CapitalInputs


class CapitalFlowEngineTest(unittest.TestCase):
    def test_scores_volume_oi_funding_and_crowding_on_zero_to_one_hundred_scale(self) -> None:
        snapshot = CapitalFlowEngine().score("run-1", CapitalInputs(
            symbol="BTCUSDT", quote_volume_24h=Decimal("150"),
            average_quote_volume_24h=Decimal("100"), oi_current=Decimal("120"),
            oi_1h_ago=Decimal("118"), oi_4h_ago=Decimal("112"), oi_24h_ago=Decimal("100"),
            current_funding_rate=Decimal("0.0001"), long_short_ratio=Decimal("1.05"),
        ))
        self.assertGreater(snapshot.capital_score, Decimal("70"))
        self.assertEqual(Decimal("20.00"), snapshot.oi_change_24h_pct)
        self.assertTrue(Decimal("0") <= snapshot.capital_score <= Decimal("100"))

    def test_extreme_positive_or_negative_funding_is_penalized(self) -> None:
        engine = CapitalFlowEngine()
        base = dict(symbol="BTCUSDT", quote_volume_24h=Decimal("100"),
                    average_quote_volume_24h=Decimal("100"), oi_current=Decimal("100"),
                    oi_1h_ago=Decimal("100"), oi_4h_ago=Decimal("100"),
                    oi_24h_ago=Decimal("100"), long_short_ratio=Decimal("1"))
        positive = engine.score("r", CapitalInputs(**base, current_funding_rate=Decimal("0.001")))
        negative = engine.score("r", CapitalInputs(**base, current_funding_rate=Decimal("-0.001")))
        self.assertEqual(Decimal("0.00"), positive.funding_score)
        self.assertEqual(Decimal("0.00"), negative.funding_score)
