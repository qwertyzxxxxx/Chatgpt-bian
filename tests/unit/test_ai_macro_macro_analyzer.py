from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.ai_macro.macro_analyzer import MacroAnalyzer, _classify
from binance_ai_trader.domain.models import Ticker24h


def _ticker(symbol: str, change: str) -> Ticker24h:
    return Ticker24h(
        symbol=symbol,
        quote_volume=Decimal("1000000000"),
        price_change_percent=Decimal(change),
        close_time_ms=1_700_000_000_000,
    )


class TestClassify(unittest.TestCase):
    def test_risk_off_when_btc_minus_5(self) -> None:
        state, grade, bias = _classify(Decimal("-5"), Decimal("-3"))
        self.assertEqual(state, "RISK_OFF")
        self.assertEqual(grade, "D")
        self.assertEqual(bias, "NO_TRADE")

    def test_risk_off_when_btc_minus_6(self) -> None:
        state, grade, bias = _classify(Decimal("-6"), Decimal("0"))
        self.assertEqual(state, "RISK_OFF")
        self.assertEqual(grade, "D")
        self.assertEqual(bias, "NO_TRADE")

    def test_bear_when_both_negative(self) -> None:
        state, grade, bias = _classify(Decimal("-3"), Decimal("-2"))
        self.assertEqual(state, "BEAR")
        self.assertEqual(grade, "C")
        self.assertEqual(bias, "SHORT_ONLY")

    def test_bear_requires_eth_threshold(self) -> None:
        state, grade, bias = _classify(Decimal("-3"), Decimal("-1"))
        self.assertEqual(state, "RANGE")

    def test_bull_grade_a_when_btc_5_plus(self) -> None:
        state, grade, bias = _classify(Decimal("5"), Decimal("3"))
        self.assertEqual(state, "BULL")
        self.assertEqual(grade, "A")
        self.assertEqual(bias, "LONG_ONLY")

    def test_bull_grade_b_when_btc_3_to_5(self) -> None:
        state, grade, bias = _classify(Decimal("3"), Decimal("2"))
        self.assertEqual(state, "BULL")
        self.assertEqual(grade, "B")
        self.assertEqual(bias, "LONG_ONLY")

    def test_range_when_small_moves(self) -> None:
        state, grade, bias = _classify(Decimal("1"), Decimal("0.5"))
        self.assertEqual(state, "RANGE")
        self.assertEqual(grade, "B")
        self.assertEqual(bias, "BOTH")

    def test_range_when_btc_up_eth_negative(self) -> None:
        state, grade, bias = _classify(Decimal("4"), Decimal("-1"))
        self.assertEqual(state, "RANGE")

    def test_bear_requires_btc_threshold(self) -> None:
        state, grade, bias = _classify(Decimal("-2"), Decimal("-3"))
        self.assertEqual(state, "RANGE")


class TestMacroAnalyzer(unittest.TestCase):
    def test_analyze_bull_market(self) -> None:
        btc = _ticker("BTCUSDT", "6")
        eth = _ticker("ETHUSDT", "4")
        now = datetime(2026, 1, 1, tzinfo=UTC)
        result = MacroAnalyzer().analyze(btc, eth, now)
        self.assertEqual(result.market_state, "BULL")
        self.assertEqual(result.risk_grade, "A")
        self.assertEqual(result.trade_bias, "LONG_ONLY")
        self.assertEqual(result.btc_change_pct, Decimal("6"))

    def test_analyze_uses_current_time_when_none(self) -> None:
        btc = _ticker("BTCUSDT", "1")
        eth = _ticker("ETHUSDT", "1")
        result = MacroAnalyzer().analyze(btc, eth, None)
        self.assertNotEqual(result.generated_at, "")

    def test_analyze_risk_off(self) -> None:
        btc = _ticker("BTCUSDT", "-7")
        eth = _ticker("ETHUSDT", "-5")
        result = MacroAnalyzer().analyze(btc, eth)
        self.assertEqual(result.market_state, "RISK_OFF")
        self.assertEqual(result.trade_bias, "NO_TRADE")

    def test_analyze_bear(self) -> None:
        btc = _ticker("BTCUSDT", "-4")
        eth = _ticker("ETHUSDT", "-3")
        result = MacroAnalyzer().analyze(btc, eth)
        self.assertEqual(result.market_state, "BEAR")
        self.assertEqual(result.trade_bias, "SHORT_ONLY")

    def test_analyze_range(self) -> None:
        btc = _ticker("BTCUSDT", "1")
        eth = _ticker("ETHUSDT", "0.5")
        result = MacroAnalyzer().analyze(btc, eth)
        self.assertEqual(result.market_state, "RANGE")
        self.assertEqual(result.trade_bias, "BOTH")

    def test_generated_at_is_iso_string(self) -> None:
        btc = _ticker("BTCUSDT", "5")
        eth = _ticker("ETHUSDT", "3")
        result = MacroAnalyzer().analyze(btc, eth)
        self.assertIn("T", result.generated_at)
        self.assertIn("+", result.generated_at)


if __name__ == "__main__":
    unittest.main()
