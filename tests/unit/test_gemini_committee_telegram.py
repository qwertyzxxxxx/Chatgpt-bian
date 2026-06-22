import unittest

from binance_ai_trader.gemini_committee.models import CommitteeDecision, SkipResult
from binance_ai_trader.gemini_committee.telegram_formatter import (
    format_skipped,
    format_trade,
)


def _trade_decision() -> CommitteeDecision:
    return CommitteeDecision(
        decision="TRADE", best_symbol="BTCUSDT", direction="LONG",
        rating="A+", entry="50000", stop_loss="48000", tp1="52000", tp2="54000",
        rr="2.00", risk_level="LOW", should_trade=True,
        reasons=["strong trend", "high volume"],
        reject_reasons=[{"symbol": "ETHUSDT", "reason": "weak RSI"}],
        data_quality="GOOD"
    )


def _no_trade_decision() -> CommitteeDecision:
    return CommitteeDecision.no_trade()


class FormatTradeTest(unittest.TestCase):
    def test_trade_message_contains_required_fields(self):
        msgs = format_trade(_trade_decision())
        self.assertGreater(len(msgs), 0)
        combined = "".join(msgs)
        self.assertIn("TRADE", combined)
        self.assertIn("BTCUSDT", combined)
        self.assertIn("LONG", combined)
        self.assertIn("A+", combined)
        self.assertIn("50000", combined)
        self.assertIn("48000", combined)
        self.assertIn("仅供研究", combined)

    def test_trade_message_contains_reject_reasons(self):
        msgs = format_trade(_trade_decision())
        combined = "".join(msgs)
        self.assertIn("ETHUSDT", combined)
        self.assertIn("weak RSI", combined)

    def test_no_trade_message_format(self):
        msgs = format_trade(_no_trade_decision())
        combined = "".join(msgs)
        self.assertIn("NO_TRADE", combined)
        self.assertIn("仅供研究", combined)

    def test_long_message_is_split(self):
        decision = CommitteeDecision(
            decision="TRADE", best_symbol="X" * 100, direction="LONG",
            rating="A", entry="1", stop_loss="0.9", tp1="1.1", tp2="1.2",
            rr="2.00", risk_level="LOW", should_trade=True,
            reasons=["r" * 500] * 10,
            reject_reasons=[{"symbol": f"SYM{i}", "reason": "r" * 200} for i in range(20)],
            data_quality="GOOD"
        )
        msgs = format_trade(decision)
        for msg in msgs:
            self.assertLessEqual(len(msg), 4096)


class FormatSkippedTest(unittest.TestCase):
    def test_cooldown_format(self):
        msgs = format_skipped(SkipResult("cooldown_active"))
        combined = "".join(msgs)
        self.assertIn("已跳过", combined)
        self.assertIn("cooldown_active", combined)

    def test_no_candidates_format(self):
        msgs = format_skipped(SkipResult("no_candidates"))
        combined = "".join(msgs)
        self.assertIn("no_candidates", combined)

    def test_api_key_missing_format(self):
        msgs = format_skipped(SkipResult("gemini_api_key_missing"))
        combined = "".join(msgs)
        self.assertIn("gemini_api_key_missing", combined)

    def test_existing_open_recommendation_format(self):
        msgs = format_skipped(SkipResult("existing_open_recommendation"))
        combined = "".join(msgs)
        self.assertIn("existing_open_recommendation", combined)


if __name__ == "__main__":
    unittest.main()
