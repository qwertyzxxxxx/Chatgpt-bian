from __future__ import annotations

import unittest
from decimal import Decimal

from binance_ai_trader.ai_macro.models import (
    AIMacroPerformance,
    AIMacroScore,
    AIMacroTrade,
    MacroAnalysis,
)


class TestMacroAnalysis(unittest.TestCase):
    def test_fields_stored(self) -> None:
        m = MacroAnalysis(
            generated_at="2026-01-01T00:00:00+00:00",
            btc_change_pct=Decimal("5.5"),
            eth_change_pct=Decimal("3.2"),
            market_state="BULL",
            risk_grade="A",
            trade_bias="LONG_ONLY",
        )
        self.assertEqual(m.market_state, "BULL")
        self.assertEqual(m.risk_grade, "A")
        self.assertEqual(m.trade_bias, "LONG_ONLY")

    def test_frozen(self) -> None:
        m = MacroAnalysis(
            generated_at="2026-01-01T00:00:00+00:00",
            btc_change_pct=Decimal("1"),
            eth_change_pct=Decimal("1"),
            market_state="RANGE",
            risk_grade="B",
            trade_bias="BOTH",
        )
        with self.assertRaises((AttributeError, TypeError)):
            m.market_state = "BULL"  # type: ignore[misc]

    def test_decimal_fields(self) -> None:
        m = MacroAnalysis(
            generated_at="2026-01-01T00:00:00+00:00",
            btc_change_pct=Decimal("-5.0"),
            eth_change_pct=Decimal("-3.0"),
            market_state="RISK_OFF",
            risk_grade="D",
            trade_bias="NO_TRADE",
        )
        self.assertIsInstance(m.btc_change_pct, Decimal)
        self.assertIsInstance(m.eth_change_pct, Decimal)


class TestAIMacroScore(unittest.TestCase):
    def test_pass_direction(self) -> None:
        s = AIMacroScore(
            symbol="XYZUSDT", direction="PASS", score=30,
            trend_score=5, momentum_score=6, volume_score=8,
            structure_score=6, risk_score=5, reason="low_score",
            entry=None, stop_loss=None, tp1=None, tp2=None,
        )
        self.assertEqual(s.direction, "PASS")
        self.assertIsNone(s.entry)

    def test_long_direction_with_levels(self) -> None:
        s = AIMacroScore(
            symbol="BTCUSDT", direction="LONG", score=85,
            trend_score=20, momentum_score=17, volume_score=20,
            structure_score=14, risk_score=14, reason="good",
            entry=Decimal("100"), stop_loss=Decimal("96"),
            tp1=Decimal("104"), tp2=Decimal("108"),
        )
        self.assertEqual(s.direction, "LONG")
        self.assertEqual(s.score, 85)
        self.assertEqual(s.entry, Decimal("100"))

    def test_score_components_present(self) -> None:
        s = AIMacroScore(
            symbol="ETHUSDT", direction="SHORT", score=82,
            trend_score=20, momentum_score=17, volume_score=15,
            structure_score=14, risk_score=16, reason="ok",
            entry=Decimal("3000"), stop_loss=Decimal("3150"),
            tp1=Decimal("2850"), tp2=Decimal("2700"),
        )
        total = s.trend_score + s.momentum_score + s.volume_score + s.structure_score + s.risk_score
        self.assertEqual(total, s.score)


class TestAIMacroTrade(unittest.TestCase):
    def test_open_trade(self) -> None:
        t = AIMacroTrade(
            trade_id="abc123",
            created_at="2026-01-01T00:00:00+00:00",
            symbol="ETHUSDT",
            direction="LONG",
            entry=Decimal("3000"),
            stop_loss=Decimal("2850"),
            tp1=Decimal("3150"),
            tp2=Decimal("3300"),
            score=82,
            market_state="BULL",
            risk_grade="A",
            reason="strong momentum",
            status="OPEN",
            pnl_pct=None,
            closed_at=None,
        )
        self.assertEqual(t.status, "OPEN")
        self.assertIsNone(t.pnl_pct)
        self.assertIsNone(t.closed_at)

    def test_closed_trade(self) -> None:
        t = AIMacroTrade(
            trade_id="def456",
            created_at="2026-01-01T00:00:00+00:00",
            symbol="SOLUSDT",
            direction="SHORT",
            entry=Decimal("200"),
            stop_loss=Decimal("210"),
            tp1=Decimal("190"),
            tp2=Decimal("180"),
            score=81,
            market_state="BEAR",
            risk_grade="C",
            reason="downtrend",
            status="TP2",
            pnl_pct=Decimal("10.00"),
            closed_at="2026-01-01T12:00:00+00:00",
        )
        self.assertEqual(t.status, "TP2")
        self.assertEqual(t.pnl_pct, Decimal("10.00"))
        self.assertIsNotNone(t.closed_at)


class TestAIMacroPerformance(unittest.TestCase):
    def test_zero_state(self) -> None:
        p = AIMacroPerformance(
            total_trades=0, open_trades=0, closed_trades=0,
            win_count=0, tp1_count=0, tp2_count=0,
            stop_count=0, expired_count=0,
            win_rate=Decimal("0"), tp1_rate=Decimal("0"), tp2_rate=Decimal("0"),
            avg_pnl_pct=Decimal("0"),
            virtual_balance=Decimal("1000"),
        )
        self.assertEqual(p.total_trades, 0)
        self.assertEqual(p.virtual_balance, Decimal("1000"))

    def test_profitable_state(self) -> None:
        p = AIMacroPerformance(
            total_trades=5, open_trades=1, closed_trades=4,
            win_count=3, tp1_count=1, tp2_count=2,
            stop_count=1, expired_count=0,
            win_rate=Decimal("75.0"), tp1_rate=Decimal("25.0"), tp2_rate=Decimal("50.0"),
            avg_pnl_pct=Decimal("4.50"),
            virtual_balance=Decimal("1036.00"),
        )
        self.assertEqual(p.win_count, 3)
        self.assertEqual(p.virtual_balance, Decimal("1036.00"))
        self.assertEqual(p.tp1_count + p.tp2_count, p.win_count)


if __name__ == "__main__":
    unittest.main()
