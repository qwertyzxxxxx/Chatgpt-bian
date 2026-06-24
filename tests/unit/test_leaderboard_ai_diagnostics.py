"""Unit tests for the leaderboard-watch AI diagnostics additions.

Covers: field-quality analyzer (UNKNOWN stats), candidate field aliases,
Gemini prompt conservative/aggressive mode, and the enriched NO_TRADE
Telegram message.
"""
from __future__ import annotations

import unittest

from binance_ai_trader.leaderboard_watch.diagnostics import (
    analyze_candidate_fields,
    top_missing_fields,
)
from binance_ai_trader.leaderboard_watch.models import (
    WatchCandidateForGemini,
    WatchDecision,
)
from binance_ai_trader.leaderboard_watch.prompt_builder import build_prompt
from binance_ai_trader.leaderboard_watch.telegram_formatter import format_review


def _full_tf() -> dict[str, str]:
    return {
        "trend": "UP",
        "rsi14": "55.0",
        "atr_pct": "1.2",
        "volume_ratio": "1.5",
        "recent_high": "100",
        "recent_low": "90",
    }


def _full_candidate(symbol: str = "BTCUSDT") -> dict:
    return {
        "symbol": symbol,
        "latest_rank_type": "GAINER",
        "latest_change_24h": "12.3",
        "quote_volume": "1000000",
        "m15": _full_tf(),
        "h1": _full_tf(),
        "h4": _full_tf(),
        "d1": _full_tf(),
    }


class AnalyzeCandidateFieldsTest(unittest.TestCase):
    def test_full_candidate_has_zero_unknown(self):
        stats = analyze_candidate_fields([_full_candidate()])
        self.assertEqual(stats["candidate_count"], 1)
        self.assertEqual(stats["unknown_fields"], 0)
        self.assertEqual(stats["unknown_ratio"], 0.0)
        self.assertEqual(stats["missing_field_counts"], {})

    def test_unknown_stats_correct(self):
        # One candidate, fully populated except two UNKNOWN/missing fields.
        cand = _full_candidate()
        cand["h1"]["atr_pct"] = "UNKNOWN"  # one UNKNOWN
        del cand["d1"]["recent_low"]  # one missing
        stats = analyze_candidate_fields([cand])

        # 4 top fields + 4 timeframes * 6 fields = 28 total fields.
        self.assertEqual(stats["total_fields"], 28)
        self.assertEqual(stats["unknown_fields"], 2)
        self.assertEqual(stats["unknown_ratio"], round(2 / 28, 4))
        self.assertEqual(stats["missing_field_counts"]["h1.atr_pct"], 1)
        self.assertEqual(stats["missing_field_counts"]["d1.recent_low"], 1)

    def test_alias_top_fields_recognized(self):
        # Canonical names (rank_type/change_24h) should also count as present.
        cand = _full_candidate()
        cand["rank_type"] = cand.pop("latest_rank_type")
        cand["change_24h"] = cand.pop("latest_change_24h")
        stats = analyze_candidate_fields([cand])
        self.assertNotIn("rank_type", stats["missing_field_counts"])
        self.assertNotIn("change_24h", stats["missing_field_counts"])

    def test_missing_top_field_counted(self):
        cand = _full_candidate()
        cand["symbol"] = ""  # blank == UNKNOWN
        stats = analyze_candidate_fields([cand])
        self.assertEqual(stats["missing_field_counts"]["symbol"], 1)

    def test_risk_distribution_by_h1_atr(self):
        low = _full_candidate("LOWUSDT")
        low["h1"]["atr_pct"] = "1.0"
        med = _full_candidate("MEDUSDT")
        med["h1"]["atr_pct"] = "3.0"
        high = _full_candidate("HIGHUSDT")
        high["h1"]["atr_pct"] = "9.0"
        unknown = _full_candidate("UNKUSDT")
        unknown["h1"]["atr_pct"] = "UNKNOWN"
        stats = analyze_candidate_fields([low, med, high, unknown])
        self.assertEqual(stats["risk_distribution"]["LOW"], 1)
        self.assertEqual(stats["risk_distribution"]["MEDIUM"], 1)
        self.assertEqual(stats["risk_distribution"]["HIGH"], 2)

    def test_empty_candidates(self):
        stats = analyze_candidate_fields([])
        self.assertEqual(stats["candidate_count"], 0)
        self.assertEqual(stats["unknown_ratio"], 0.0)

    def test_top_missing_fields_sorted(self):
        counts = {"a": 1, "b": 3, "c": 3, "d": 2}
        top = top_missing_fields(counts, n=2)
        # Highest count first; ties broken by field name.
        self.assertEqual(top, [("b", 3), ("c", 3)])


class PromptModeTest(unittest.TestCase):
    def _candidate(self) -> WatchCandidateForGemini:
        return WatchCandidateForGemini(
            symbol="BTCUSDT",
            latest_rank_type="GAINER",
            latest_rank_position=1,
            best_rank_position=1,
            latest_change_24h="12.3",
            first_change_24h="5.0",
            quote_volume="1000000",
            active_duration_minutes=120,
            appearances_24h=3,
            gainer_candidate=True,
            loser_candidate=False,
            volume_candidate=False,
        )

    def test_conservative_default_unchanged(self):
        default = build_prompt([self._candidate()])
        conservative = build_prompt([self._candidate()], mode="conservative")
        self.assertEqual(default, conservative)
        self.assertNotIn("进取模式", default)

    def test_aggressive_appends_addendum(self):
        aggressive = build_prompt([self._candidate()], mode="aggressive")
        self.assertIn("进取模式", aggressive)
        # Aggressive must still forbid no-stop-loss recommendations.
        self.assertIn("止损", aggressive)


class NoTradeTelegramStatsTest(unittest.TestCase):
    def _no_trade(self) -> WatchDecision:
        d = WatchDecision.no_trade()
        d.reasons = ["数据不足", "RR 太低"]
        d.reject_reasons = [
            {"symbol": "AAA", "reason": "数据缺失"},
            {"symbol": "BBB", "reason": "数据缺失"},
            {"symbol": "CCC", "reason": "RR 太低"},
        ]
        return d

    def test_no_stats_preserves_legacy_message(self):
        msgs = format_review(self._no_trade())
        body = "\n".join(msgs)
        self.assertIn("NO_TRADE", body)
        self.assertNotIn("── 诊断 ──", body)

    def test_stats_appends_diagnostics(self):
        stats = {
            "candidate_count": 7,
            "risk_distribution": {"HIGH": 4, "MEDIUM": 2, "LOW": 1},
            "missing_field_counts": {"h1.atr_pct": 5, "d1.recent_low": 3},
        }
        msgs = format_review(self._no_trade(), stats=stats)
        body = "\n".join(msgs)
        self.assertIn("── 诊断 ──", body)
        self.assertIn("候选数量：7", body)
        self.assertIn("HIGH 4", body)
        self.assertIn("数据缺失 ×2", body)
        self.assertIn("h1.atr_pct ×5", body)


class DiagnosticPerReviewTest(unittest.TestCase):
    def _run_diagnostic(self, db_path: str) -> str:
        import argparse
        import contextlib
        import io

        from binance_ai_trader.entrypoints.cli import _leaderboard_watch_diagnostic

        args = argparse.Namespace(database=db_path, limit=20, send_telegram=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _leaderboard_watch_diagnostic(args)
        return buf.getvalue()

    def test_per_review_reject_count_from_reject_reasons(self):
        import json
        import tempfile
        from pathlib import Path

        from binance_ai_trader.leaderboard_watch.repository import (
            LeaderboardWatchRepository,
        )

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "lw.db")
            repo = LeaderboardWatchRepository(db_path)
            try:
                decision = WatchDecision.no_trade("raw")
                decision.reasons = ["仅一条总体理由"]  # generic reasons: len 1
                # field_stats carries the per-symbol reject count (3) + UNKNOWN ratio.
                fs = {
                    "candidate_count": 5,
                    "unknown_ratio": 0.2,
                    "reject_reasons_count": 3,
                }
                repo.save_review("rev-diag", decision, field_stats=json.dumps(fs))
            finally:
                repo.close()

            out = self._run_diagnostic(db_path)
            # reject count must come from reject_reasons_count (3), not reasons (1).
            self.assertIn("拒因3", out)
            self.assertIn("候选5", out)
            self.assertIn("UNKNOWN20.0%", out)
            self.assertIn("逐条审查", out)
            # NO_TRADE with UNKNOWN entry/stop_loss and HIGH risk -> flags present.
            self.assertIn("missing_trade_plan", out)
            self.assertIn("risk-HIGH", out)

    def test_per_review_falls_back_to_reasons_for_old_review(self):
        import tempfile
        from pathlib import Path

        from binance_ai_trader.leaderboard_watch.repository import (
            LeaderboardWatchRepository,
        )

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "lw.db")
            repo = LeaderboardWatchRepository(db_path)
            try:
                decision = WatchDecision.no_trade("raw")
                decision.reasons = ["理由A", "理由B"]  # len 2
                repo.save_review("rev-old", decision)  # no field_stats
            finally:
                repo.close()

            out = self._run_diagnostic(db_path)
            self.assertIn("拒因2", out)
            self.assertIn("UNKNOWNN/A", out)


if __name__ == "__main__":
    unittest.main()
