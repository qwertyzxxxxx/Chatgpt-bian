from __future__ import annotations

import unittest
from pathlib import Path

from binance_ai_trader.entrypoints.cli import build_parser


class GeminiCommitteeCLIRegistrationTest(unittest.TestCase):
    def _parse(self, *args: str):
        return build_parser().parse_args(list(args))

    def test_command_registered(self):
        a = self._parse("gemini-committee", "review")
        self.assertEqual(a.command, "gemini-committee")
        self.assertEqual(a.gc_command, "review")

    def test_review_defaults(self):
        a = self._parse("gemini-committee", "review")
        self.assertEqual(a.database, Path("data/market_data.db"))
        self.assertEqual(a.ai_macro_database, Path("data/ai_macro.db"))
        self.assertEqual(a.max_candidates, 4)
        self.assertAlmostEqual(a.cooldown_hours, 4.0)
        self.assertEqual(a.gemini_model, "gemini-2.5-flash")

    def test_review_has_max_candidates(self):
        a = self._parse("gemini-committee", "review", "--max-candidates", "6")
        self.assertEqual(a.max_candidates, 6)

    def test_review_has_cooldown_hours(self):
        a = self._parse("gemini-committee", "review", "--cooldown-hours", "2.0")
        self.assertAlmostEqual(a.cooldown_hours, 2.0)

    def test_review_has_gemini_model(self):
        a = self._parse("gemini-committee", "review", "--gemini-model", "gemini-2.5-pro")
        self.assertEqual(a.gemini_model, "gemini-2.5-pro")

    def test_review_has_ai_macro_database(self):
        a = self._parse("gemini-committee", "review", "--ai-macro-database", "/tmp/x.db")
        self.assertEqual(a.ai_macro_database, Path("/tmp/x.db"))

    def test_review_send_telegram_default_false(self):
        a = self._parse("gemini-committee", "review")
        self.assertFalse(a.send_telegram)

    def test_review_send_telegram_flag(self):
        a = self._parse("gemini-committee", "review", "--send-telegram")
        self.assertTrue(a.send_telegram)

    def test_review_has_telegram_arguments(self):
        a = self._parse(
            "gemini-committee", "review",
            "--telegram-bot-token", "TOK",
            "--telegram-chat-id", "CID",
        )
        self.assertEqual(a.telegram_bot_token, "TOK")
        self.assertEqual(a.telegram_chat_id, "CID")

    def test_review_log_level_default(self):
        a = self._parse("gemini-committee", "review")
        self.assertEqual(a.log_level, "INFO")

    def test_review_log_level_custom(self):
        a = self._parse("gemini-committee", "review", "--log-level", "DEBUG")
        self.assertEqual(a.log_level, "DEBUG")


if __name__ == "__main__":
    unittest.main()
