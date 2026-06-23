"""Unit tests for startup_report.py"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class BuildStartupMessageTest(unittest.TestCase):
    def _build(self, enabled: dict | None = None) -> str:
        from binance_ai_trader.runner.startup_report import build_startup_message
        return build_startup_message(
            db_path="data/market_data.db",
            enabled_modules=enabled or {},
        )

    def test_contains_header(self):
        msg = self._build()
        self.assertIn("🚀 Binance AI Trader Started", msg)

    def test_contains_database(self):
        msg = self._build()
        self.assertIn("data/market_data.db", msg)

    def test_contains_pid(self):
        import os
        msg = self._build()
        self.assertIn(str(os.getpid()), msg)

    def test_module_on_off(self):
        msg = self._build({"hotlist_alert": True, "gemini_committee": False})
        self.assertIn("Hotlist Alert: ON", msg)
        self.assertIn("Gemini Committee: OFF", msg)

    def test_all_modules_off_by_default(self):
        msg = self._build({})
        self.assertIn("Hotlist Alert: OFF", msg)
        self.assertIn("Leaderboard Watch: OFF", msg)

    def test_contains_research_disclaimer(self):
        msg = self._build()
        self.assertIn("仅供研究", msg)

    def test_git_sha_present(self):
        msg = self._build()
        self.assertIn("Git SHA:", msg)

    def test_branch_present(self):
        msg = self._build()
        self.assertIn("Branch:", msg)

    def test_git_sha_fallback_on_error(self):
        from binance_ai_trader.runner.startup_report import _git_sha
        with patch("subprocess.check_output", side_effect=Exception("no git")):
            sha = _git_sha()
        self.assertEqual(sha, "unknown")

    def test_git_branch_fallback_on_error(self):
        from binance_ai_trader.runner.startup_report import _git_branch
        with patch("subprocess.check_output", side_effect=Exception("no git")):
            branch = _git_branch()
        self.assertEqual(branch, "unknown")

    def test_env_id_uses_repl_id(self):
        from binance_ai_trader.runner.startup_report import _env_id
        with patch.dict("os.environ", {"REPL_ID": "my-repl-123"}, clear=False):
            env = _env_id()
        self.assertEqual(env, "my-repl-123")

    def test_env_id_fallback(self):
        from binance_ai_trader.runner.startup_report import _env_id
        with patch.dict("os.environ", {}, clear=True):
            env = _env_id()
        self.assertEqual(env, "unknown")

    def test_send_startup_report_swallows_network_error(self):
        from binance_ai_trader.runner.startup_report import send_startup_report
        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            send_startup_report(
                db_path="data/test.db",
                enabled_modules={},
                bot_token="fake-token",
                chat_id="fake-chat",
            )


if __name__ == "__main__":
    unittest.main()
