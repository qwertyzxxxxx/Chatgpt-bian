from __future__ import annotations

import unittest
from decimal import Decimal

from binance_ai_trader.ai_macro.models import (
    AIMacroPerformance,
    AIMacroTrade,
    MacroAnalysis,
)
from binance_ai_trader.ai_macro.telegram import (
    format_ai_macro_performance_message,
    format_ai_macro_review_message,
    format_ai_macro_scan_message,
    format_ai_macro_settle_message,
)


def _analysis(market_state: str = "BULL", trade_bias: str = "LONG_ONLY") -> MacroAnalysis:
    return MacroAnalysis(
        generated_at="2026-01-01T00:00:00+00:00",
        btc_change_pct=Decimal("5"),
        eth_change_pct=Decimal("3"),
        market_state=market_state,
        risk_grade="A",
        trade_bias=trade_bias,
    )


def _trade(
    symbol: str = "BTCUSDT",
    direction: str = "LONG",
    status: str = "OPEN",
    score: int = 83,
) -> AIMacroTrade:
    return AIMacroTrade(
        trade_id="t1",
        created_at="2026-01-01T00:00:00+00:00",
        symbol=symbol,
        direction=direction,
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        tp1=Decimal("105"),
        tp2=Decimal("110"),
        score=score,
        market_state="BULL",
        risk_grade="A",
        reason="test reason",
        status=status,
        pnl_pct=Decimal("5") if status != "OPEN" else None,
        closed_at="2026-01-02T00:00:00+00:00" if status != "OPEN" else None,
    )


class TestFormatAIMacroScanMessage(unittest.TestCase):
    def test_contains_header(self) -> None:
        msg = format_ai_macro_scan_message(_analysis(), [])
        self.assertIn("AI Macro Report", msg)

    def test_no_trades_shows_no_opportunity(self) -> None:
        msg = format_ai_macro_scan_message(_analysis(), [])
        self.assertIn("无新机会", msg)

    def test_with_trades_shows_symbol(self) -> None:
        msg = format_ai_macro_scan_message(_analysis(), [_trade()])
        self.assertIn("BTCUSDT", msg)
        self.assertIn("Top Opportunity #1", msg)

    def test_multiple_trades_numbered(self) -> None:
        trades = [_trade("BTCUSDT"), _trade("ETHUSDT", score=82)]
        msg = format_ai_macro_scan_message(_analysis(), trades)
        self.assertIn("Top Opportunity #1", msg)
        self.assertIn("Top Opportunity #2", msg)

    def test_contains_score(self) -> None:
        msg = format_ai_macro_scan_message(_analysis(), [_trade()])
        self.assertIn("83/100", msg)

    def test_research_only_disclaimer(self) -> None:
        msg = format_ai_macro_scan_message(_analysis(), [])
        self.assertIn("Research Only", msg)

    def test_btc_eth_change_shown(self) -> None:
        msg = format_ai_macro_scan_message(_analysis(), [])
        self.assertIn("BTC", msg)
        self.assertIn("ETH", msg)

    def test_bear_state_shown(self) -> None:
        msg = format_ai_macro_scan_message(_analysis("BEAR", "SHORT_ONLY"), [])
        self.assertIn("BEAR", msg)

    def test_risk_off_shown(self) -> None:
        msg = format_ai_macro_scan_message(_analysis("RISK_OFF", "NO_TRADE"), [])
        self.assertIn("RISK_OFF", msg)

    def test_trade_entry_shown(self) -> None:
        msg = format_ai_macro_scan_message(_analysis(), [_trade()])
        self.assertIn("买入", msg)
        self.assertIn("止损", msg)


class TestFormatAIMacroReviewMessage(unittest.TestCase):
    def test_empty_trades(self) -> None:
        msg = format_ai_macro_review_message([], {}, "2026-01-01T12:00:00+00:00")
        self.assertIn("无持仓", msg)

    def test_shows_symbol(self) -> None:
        msg = format_ai_macro_review_message(
            [_trade()], {"BTCUSDT": Decimal("103")}, "2026-01-01T12:00:00+00:00"
        )
        self.assertIn("BTCUSDT", msg)

    def test_shows_pnl_when_price_provided(self) -> None:
        msg = format_ai_macro_review_message(
            [_trade()], {"BTCUSDT": Decimal("105")}, "2026-01-01T12:00:00+00:00"
        )
        self.assertIn("当前盈亏", msg)

    def test_no_price_still_renders(self) -> None:
        msg = format_ai_macro_review_message([_trade()], {}, "2026-01-01T12:00:00+00:00")
        self.assertIn("BTCUSDT", msg)
        self.assertIn("Research Only", msg)

    def test_research_only_disclaimer(self) -> None:
        msg = format_ai_macro_review_message([], {}, "2026-01-01T12:00:00+00:00")
        self.assertIn("Research Only", msg)

    def test_short_trade_pnl(self) -> None:
        msg = format_ai_macro_review_message(
            [_trade(direction="SHORT")],
            {"BTCUSDT": Decimal("95")},
            "2026-01-01T12:00:00+00:00",
        )
        self.assertIn("当前盈亏", msg)


class TestFormatAIMacroSettleMessage(unittest.TestCase):
    def test_no_settled_trades(self) -> None:
        msg = format_ai_macro_settle_message([])
        self.assertIn("无到期交易", msg)

    def test_shows_symbol_and_result(self) -> None:
        t = _trade(status="EXPIRED")
        msg = format_ai_macro_settle_message([t])
        self.assertIn("BTCUSDT", msg)
        self.assertIn("EXPIRED", msg)

    def test_shows_pnl(self) -> None:
        t = _trade(status="TP2")
        msg = format_ai_macro_settle_message([t])
        self.assertIn("最终收益", msg)

    def test_research_only_disclaimer(self) -> None:
        msg = format_ai_macro_settle_message([])
        self.assertIn("Research Only", msg)


class TestFormatAIMacroPerformanceMessage(unittest.TestCase):
    def test_contains_key_fields(self) -> None:
        perf = AIMacroPerformance(
            total_trades=5, open_trades=1, closed_trades=4,
            win_count=3, tp1_count=1, tp2_count=2,
            stop_count=1, expired_count=0,
            win_rate=Decimal("75.0"), tp1_rate=Decimal("25.0"),
            tp2_rate=Decimal("50.0"), avg_pnl_pct=Decimal("4.5"),
            virtual_balance=Decimal("1036.00"),
        )
        msg = format_ai_macro_performance_message(perf)
        self.assertIn("AI Macro Performance", msg)
        self.assertIn("胜率", msg)
        self.assertIn("虚拟账户", msg)
        self.assertIn("1036.00", msg)
        self.assertIn("Research Only", msg)

    def test_zero_performance(self) -> None:
        perf = AIMacroPerformance(
            total_trades=0, open_trades=0, closed_trades=0,
            win_count=0, tp1_count=0, tp2_count=0,
            stop_count=0, expired_count=0,
            win_rate=Decimal("0.0"), tp1_rate=Decimal("0.0"),
            tp2_rate=Decimal("0.0"), avg_pnl_pct=Decimal("0.0"),
            virtual_balance=Decimal("1000.00"),
        )
        msg = format_ai_macro_performance_message(perf)
        self.assertIn("0", msg)


if __name__ == "__main__":
    unittest.main()
