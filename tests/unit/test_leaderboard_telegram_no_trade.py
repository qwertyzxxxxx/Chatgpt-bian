"""Unit tests for leaderboard_watch/telegram_formatter.py — NO_TRADE enrichment."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from binance_ai_trader.leaderboard_watch.models import WatchDecision
from binance_ai_trader.leaderboard_watch.telegram_formatter import format_review


def _no_trade_decision(
    reasons: list[str] | None = None,
    reject_reasons: list[dict] | None = None,
    data_quality: str = "PARTIAL",
    risk_level: str = "HIGH",
) -> WatchDecision:
    return WatchDecision(
        decision="NO_TRADE",
        best_symbol="NONE",
        direction="UNKNOWN",
        entry="UNKNOWN",
        stop_loss="UNKNOWN",
        tp1="UNKNOWN",
        tp2="UNKNOWN",
        rr="UNKNOWN",
        rating="C",
        risk_level=risk_level,
        should_trade=False,
        reasons=reasons or ["数据不足，无法做出决策"],
        reject_reasons=reject_reasons or [],
        data_quality=data_quality,
        raw_response="{}",
    )


def _trade_decision() -> WatchDecision:
    return WatchDecision(
        decision="TRADE",
        best_symbol="ETHUSDT",
        direction="LONG",
        entry="3000",
        stop_loss="2900",
        tp1="3100",
        tp2="3200",
        rr="2.0",
        rating="A",
        risk_level="MEDIUM",
        should_trade=True,
        reasons=["趋势向上"],
        reject_reasons=[{"symbol": "BTCUSDT", "reason": "RR不足"}],
        data_quality="GOOD",
        raw_response="{}",
    )


class TestFormatReviewNoTradeBasic(unittest.TestCase):
    def test_no_trade_returns_list_of_strings(self):
        msgs = format_review(_no_trade_decision())
        self.assertIsInstance(msgs, list)
        self.assertGreater(len(msgs), 0)
        self.assertTrue(all(isinstance(m, str) for m in msgs))

    def test_no_trade_contains_no_trade_text(self):
        msgs = format_review(_no_trade_decision())
        combined = "\n".join(msgs)
        self.assertIn("NO_TRADE", combined)

    def test_no_trade_no_stats_no_diagnostic_block(self):
        """When stats=None, no diagnostic block appended."""
        msgs = format_review(_no_trade_decision(), stats=None)
        combined = "\n".join(msgs)
        self.assertNotIn("候选数量", combined)
        self.assertNotIn("缺失字段", combined)

    def test_no_trade_with_empty_stats_no_diagnostic(self):
        msgs = format_review(_no_trade_decision(), stats={})
        combined = "\n".join(msgs)
        self.assertNotIn("候选数量", combined)


class TestFormatReviewNoTradeWithStats(unittest.TestCase):
    def _stats(
        self,
        candidate_count: int = 5,
        unknown_ratio: float = 0.3,
        risk: dict | None = None,
        missing: dict | None = None,
        reject: list | None = None,
    ) -> dict:
        return {
            "candidate_count": candidate_count,
            "unknown_ratio": unknown_ratio,
            "risk_distribution": risk or {"HIGH": 2, "MEDIUM": 2, "LOW": 1},
            "missing_field_counts": missing or {"m15.trend": 3, "h1.rsi14": 2},
        }

    def test_stats_shows_candidate_count(self):
        msgs = format_review(_no_trade_decision(), stats=self._stats(candidate_count=8))
        combined = "\n".join(msgs)
        self.assertIn("8", combined)

    def test_stats_shows_risk_distribution(self):
        msgs = format_review(_no_trade_decision(), stats=self._stats())
        combined = "\n".join(msgs)
        self.assertIn("HIGH", combined)
        self.assertIn("MEDIUM", combined)
        self.assertIn("LOW", combined)

    def test_stats_shows_missing_fields(self):
        msgs = format_review(
            _no_trade_decision(),
            stats=self._stats(missing={"m15.trend": 5, "h1.rsi14": 3})
        )
        combined = "\n".join(msgs)
        self.assertIn("m15.trend", combined)
        self.assertIn("缺失字段", combined)

    def test_stats_shows_top_reject_reasons(self):
        decision = _no_trade_decision(
            reject_reasons=[
                {"symbol": "BTC", "reason": "RR不足"},
                {"symbol": "ETH", "reason": "RR不足"},
                {"symbol": "SOL", "reason": "数据缺失"},
            ]
        )
        msgs = format_review(decision, stats=self._stats())
        combined = "\n".join(msgs)
        self.assertIn("RR不足", combined)


class TestFormatReviewTrade(unittest.TestCase):
    def test_trade_no_diagnostic_section(self):
        """TRADE decisions must not show the diagnostic section."""
        msgs = format_review(_trade_decision(), stats={"candidate_count": 5})
        combined = "\n".join(msgs)
        self.assertNotIn("候选数量", combined)

    def test_trade_shows_symbol_and_direction(self):
        msgs = format_review(_trade_decision())
        combined = "\n".join(msgs)
        self.assertIn("ETHUSDT", combined)
        self.assertIn("LONG", combined)

    def test_trade_shows_reject_reasons(self):
        msgs = format_review(_trade_decision())
        combined = "\n".join(msgs)
        self.assertIn("BTCUSDT", combined)
        self.assertIn("RR不足", combined)

    def test_disclaimer_always_present(self):
        for decision in (_no_trade_decision(), _trade_decision()):
            msgs = format_review(decision)
            combined = "\n".join(msgs)
            self.assertIn("仅供研究", combined)
