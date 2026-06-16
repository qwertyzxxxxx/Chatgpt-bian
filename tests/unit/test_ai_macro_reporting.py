from __future__ import annotations

import unittest
from decimal import Decimal

from binance_ai_trader.ai_macro.models import (
    AIMacroScore,
    AIMacroTrade,
    MacroAnalysis,
)
from binance_ai_trader.ai_macro.reporting import (
    calculate_performance,
    render_ai_macro_performance,
    render_ai_macro_report,
)


def _analysis(market_state: str = "BULL", risk_grade: str = "A", trade_bias: str = "LONG_ONLY") -> MacroAnalysis:
    return MacroAnalysis(
        generated_at="2026-01-01T00:00:00+00:00",
        btc_change_pct=Decimal("5"),
        eth_change_pct=Decimal("3"),
        market_state=market_state,
        risk_grade=risk_grade,
        trade_bias=trade_bias,
    )


def _trade(
    trade_id: str = "t1",
    symbol: str = "BTCUSDT",
    direction: str = "LONG",
    status: str = "OPEN",
    pnl_pct: Decimal | None = None,
    score: int = 82,
) -> AIMacroTrade:
    return AIMacroTrade(
        trade_id=trade_id,
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
        reason="test",
        status=status,
        pnl_pct=pnl_pct,
        closed_at="2026-01-02T00:00:00+00:00" if pnl_pct is not None else None,
    )


def _score(symbol: str = "BTCUSDT", direction: str = "LONG", score: int = 82) -> AIMacroScore:
    return AIMacroScore(
        symbol=symbol, direction=direction, score=score,
        trend_score=20, momentum_score=17, volume_score=20,
        structure_score=14, risk_score=11, reason="good",
        entry=Decimal("100"), stop_loss=Decimal("95"),
        tp1=Decimal("105"), tp2=Decimal("110"),
    )


class TestCalculatePerformance(unittest.TestCase):
    def test_empty_trades(self) -> None:
        perf = calculate_performance(())
        self.assertEqual(perf.total_trades, 0)
        self.assertEqual(perf.virtual_balance, Decimal("1000"))
        self.assertEqual(perf.win_rate, Decimal("0.0"))

    def test_all_open(self) -> None:
        trades = (_trade("t1"), _trade("t2"))
        perf = calculate_performance(trades)
        self.assertEqual(perf.total_trades, 2)
        self.assertEqual(perf.open_trades, 2)
        self.assertEqual(perf.closed_trades, 0)
        self.assertEqual(perf.virtual_balance, Decimal("1000.00"))

    def test_tp2_win_increases_balance(self) -> None:
        t = _trade("t1", status="TP2", pnl_pct=Decimal("10"))
        perf = calculate_performance((t,))
        self.assertEqual(perf.tp2_count, 1)
        self.assertEqual(perf.win_count, 1)
        self.assertEqual(perf.virtual_balance, Decimal("1020.00"))

    def test_stop_decreases_balance(self) -> None:
        t = _trade("t1", status="STOP", pnl_pct=Decimal("-5"))
        perf = calculate_performance((t,))
        self.assertEqual(perf.stop_count, 1)
        self.assertEqual(perf.win_count, 0)
        self.assertEqual(perf.virtual_balance, Decimal("990.00"))

    def test_win_rate_calculation(self) -> None:
        trades = (
            _trade("t1", status="TP1", pnl_pct=Decimal("5")),
            _trade("t2", status="TP2", pnl_pct=Decimal("10")),
            _trade("t3", status="STOP", pnl_pct=Decimal("-5")),
            _trade("t4", status="EXPIRED", pnl_pct=Decimal("1")),
        )
        perf = calculate_performance(trades)
        self.assertEqual(perf.win_count, 2)
        self.assertEqual(perf.win_rate, Decimal("50.0"))
        self.assertEqual(perf.tp1_count, 1)
        self.assertEqual(perf.tp2_count, 1)
        self.assertEqual(perf.stop_count, 1)
        self.assertEqual(perf.expired_count, 1)

    def test_avg_pnl(self) -> None:
        trades = (
            _trade("t1", status="TP1", pnl_pct=Decimal("5")),
            _trade("t2", status="TP2", pnl_pct=Decimal("10")),
        )
        perf = calculate_performance(trades)
        self.assertEqual(perf.avg_pnl_pct, Decimal("7.50"))

    def test_virtual_balance_multiple_trades(self) -> None:
        trades = (
            _trade("t1", status="TP1", pnl_pct=Decimal("5")),
            _trade("t2", status="STOP", pnl_pct=Decimal("-5")),
        )
        perf = calculate_performance(trades)
        self.assertEqual(perf.virtual_balance, Decimal("1000.00"))


class TestRenderAIMacroReport(unittest.TestCase):
    def test_contains_market_state(self) -> None:
        result = render_ai_macro_report(_analysis(), [], [], 0)
        self.assertIn("BULL", result)
        self.assertIn("LONG_ONLY", result)

    def test_contains_score_table(self) -> None:
        scores = [_score(), _score("ETHUSDT", "SHORT", 45)]
        result = render_ai_macro_report(_analysis(), scores, [], 0)
        self.assertIn("BTCUSDT", result)
        self.assertIn("ETHUSDT", result)

    def test_contains_new_trade(self) -> None:
        trades = [_trade("t1")]
        result = render_ai_macro_report(_analysis(), [], trades, 0)
        self.assertIn("BTCUSDT", result)
        self.assertIn("82/100", result)

    def test_no_trades_note(self) -> None:
        result = render_ai_macro_report(_analysis(), [], [], 0)
        self.assertIn("No new virtual trades", result)

    def test_research_only_disclaimer(self) -> None:
        result = render_ai_macro_report(_analysis(), [], [], 0)
        self.assertIn("Research only", result)

    def test_skipped_count_shown(self) -> None:
        result = render_ai_macro_report(_analysis(), [], [], 5)
        self.assertIn("5", result)


class TestRenderAIMacroPerformance(unittest.TestCase):
    def test_renders_all_fields(self) -> None:
        trades = (
            _trade("t1", status="TP1", pnl_pct=Decimal("5")),
            _trade("t2", status="STOP", pnl_pct=Decimal("-5")),
        )
        perf = calculate_performance(trades)
        result = render_ai_macro_performance(perf)
        self.assertIn("Win Rate", result)
        self.assertIn("Virtual Balance", result)
        self.assertIn("Research only", result)

    def test_renders_zero_state(self) -> None:
        perf = calculate_performance(())
        result = render_ai_macro_performance(perf)
        self.assertIn("0", result)

    def test_renders_tp_counts(self) -> None:
        trades = (
            _trade("t1", status="TP1", pnl_pct=Decimal("5")),
            _trade("t2", status="TP2", pnl_pct=Decimal("10")),
        )
        perf = calculate_performance(trades)
        result = render_ai_macro_performance(perf)
        self.assertIn("TP1", result)
        self.assertIn("TP2", result)


if __name__ == "__main__":
    unittest.main()
