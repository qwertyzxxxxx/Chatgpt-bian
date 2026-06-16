import unittest

from binance_ai_trader.gemini_committee.models import (
    Candidate,
    CommitteeDecision,
    SkipResult,
    TimeframeIndicators,
)


class TimeframeIndicatorsTest(unittest.TestCase):
    def test_defaults_are_unknown(self):
        ti = TimeframeIndicators()
        d = ti.to_dict()
        self.assertEqual(d["trend"], "UNKNOWN")
        self.assertEqual(d["ema10"], "UNKNOWN")
        self.assertEqual(d["rsi14"], "UNKNOWN")

    def test_to_dict_contains_all_keys(self):
        ti = TimeframeIndicators(trend="UP", ema10="100.0")
        d = ti.to_dict()
        self.assertIn("ema20", d)
        self.assertIn("atr_pct", d)


class CandidateTest(unittest.TestCase):
    def test_to_dict_has_required_fields(self):
        c = Candidate(
            symbol="BTCUSDT", source="hotlist", direction="LONG",
            entry="50000", stop_loss="48000", tp1="52000", tp2="54000", rr="2.00"
        )
        d = c.to_dict()
        for key in ("symbol", "source", "direction", "entry", "stop_loss", "tp1", "tp2", "rr",
                    "m15", "h1", "h4", "d1", "hotlist_rank"):
            self.assertIn(key, d)

    def test_timeframe_dicts_nested(self):
        c = Candidate(
            symbol="ETHUSDT", source="ai_macro", direction="SHORT",
            entry="3000", stop_loss="3100", tp1="2900", tp2="2800", rr="2.00"
        )
        d = c.to_dict()
        self.assertIsInstance(d["m15"], dict)
        self.assertIn("trend", d["m15"])


class CommitteeDecisionTest(unittest.TestCase):
    def test_no_trade_factory(self):
        d = CommitteeDecision.no_trade("raw")
        self.assertEqual(d.decision, "NO_TRADE")
        self.assertEqual(d.best_symbol, "NONE")
        self.assertFalse(d.should_trade)

    def test_trade_decision_fields(self):
        d = CommitteeDecision(
            decision="TRADE", best_symbol="BTCUSDT", direction="LONG",
            rating="A", entry="50000", stop_loss="48000", tp1="52000", tp2="54000",
            rr="2.00", risk_level="MEDIUM", should_trade=True,
            reasons=["good trend"], reject_reasons=[], data_quality="GOOD"
        )
        self.assertTrue(d.should_trade)
        self.assertEqual(d.rating, "A")


class SkipResultTest(unittest.TestCase):
    def test_to_dict(self):
        s = SkipResult("cooldown_active")
        d = s.to_dict()
        self.assertEqual(d["status"], "SKIPPED")
        self.assertEqual(d["reason"], "cooldown_active")


if __name__ == "__main__":
    unittest.main()
