"""Unit tests for leaderboard_watch/prompt_builder.py — mode parameter."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from binance_ai_trader.leaderboard_watch.prompt_builder import build_prompt


def _make_candidate(symbol: str = "BTCUSDT") -> MagicMock:
    c = MagicMock()
    c.to_dict.return_value = {
        "symbol": symbol,
        "rank_type": "GAINER",
        "change_24h": "12.5",
    }
    return c


class TestBuildPromptConservative(unittest.TestCase):
    def test_default_is_conservative(self):
        """Default mode must be conservative (no aggressive addendum)."""
        prompt = build_prompt([_make_candidate()])
        self.assertNotIn("aggressive", prompt.lower())
        self.assertNotIn("进取模式", prompt)

    def test_explicit_conservative(self):
        prompt = build_prompt([_make_candidate()], mode="conservative")
        self.assertNotIn("进取模式", prompt)

    def test_contains_candidate_data(self):
        prompt = build_prompt([_make_candidate("ETHUSDT")])
        self.assertIn("ETHUSDT", prompt)


class TestBuildPromptAggressive(unittest.TestCase):
    def test_aggressive_addendum_present(self):
        prompt = build_prompt([_make_candidate()], mode="aggressive")
        self.assertIn("进取模式", prompt)
        self.assertIn("aggressive", prompt)

    def test_aggressive_keeps_stop_loss_rule(self):
        """Aggressive prompt must still require stop-loss."""
        prompt = build_prompt([_make_candidate()], mode="aggressive")
        self.assertIn("止损", prompt)
        self.assertIn("NO_TRADE", prompt)

    def test_aggressive_does_not_force_trade(self):
        """Aggressive mode must not contain language that forces TRADE."""
        prompt = build_prompt([_make_candidate()], mode="aggressive")
        # The mandatory rules section should still be present
        self.assertIn("should_trade = false", prompt)

    def test_conservative_and_aggressive_share_base_rules(self):
        """Both modes share the same system rules block."""
        p_cons = build_prompt([_make_candidate()], mode="conservative")
        p_aggr = build_prompt([_make_candidate()], mode="aggressive")
        # The base system is a prefix of the aggressive prompt
        self.assertTrue(p_aggr.startswith(p_cons[:200]))


class TestBuildPromptMultipleCandidates(unittest.TestCase):
    def test_all_candidates_in_prompt(self):
        cands = [_make_candidate(s) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
        prompt = build_prompt(cands)
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            self.assertIn(sym, prompt)

    def test_empty_candidates(self):
        prompt = build_prompt([])
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 0)
