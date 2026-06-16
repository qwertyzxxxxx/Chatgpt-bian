from __future__ import annotations

import sys
import unittest
from pathlib import Path

from binance_ai_trader.entrypoints.cli import build_parser


class TestAIMacroCLIParser(unittest.TestCase):
    def test_scan_defaults(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan"])
        self.assertEqual(args.command, "ai-macro")
        self.assertEqual(args.ai_macro_command, "scan")
        self.assertEqual(args.database, Path("data/ai_macro.db"))
        self.assertEqual(args.gainers, 6)
        self.assertEqual(args.losers, 6)

    def test_scan_custom_database(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan", "--database", "/tmp/test.db"])
        self.assertEqual(args.database, Path("/tmp/test.db"))

    def test_scan_telegram_arguments(self) -> None:
        args = build_parser().parse_args([
            "ai-macro", "scan",
            "--telegram-bot-token", "TOKEN123",
            "--telegram-chat-id", "CHAT456",
        ])
        self.assertEqual(args.telegram_bot_token, "TOKEN123")
        self.assertEqual(args.telegram_chat_id, "CHAT456")

    def test_scan_send_telegram_flag(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan", "--send-telegram"])
        self.assertTrue(args.send_telegram)

    def test_scan_default_no_send_telegram(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan"])
        self.assertFalse(args.send_telegram)

    def test_review_defaults(self) -> None:
        args = build_parser().parse_args(["ai-macro", "review"])
        self.assertEqual(args.ai_macro_command, "review")
        self.assertEqual(args.database, Path("data/ai_macro.db"))

    def test_settle_defaults(self) -> None:
        args = build_parser().parse_args(["ai-macro", "settle"])
        self.assertEqual(args.ai_macro_command, "settle")
        self.assertEqual(args.database, Path("data/ai_macro.db"))

    def test_performance_defaults(self) -> None:
        args = build_parser().parse_args(["ai-macro", "performance"])
        self.assertEqual(args.ai_macro_command, "performance")
        self.assertEqual(args.database, Path("data/ai_macro.db"))
        self.assertEqual(args.report, Path("reports/ai_macro_performance.md"))

    def test_scan_report_default(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan"])
        self.assertEqual(args.report, Path("reports/ai_macro_report.md"))

    def test_scan_gainers_losers_custom(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan", "--gainers", "3", "--losers", "4"])
        self.assertEqual(args.gainers, 3)
        self.assertEqual(args.losers, 4)

    def test_scan_base_url_custom(self) -> None:
        args = build_parser().parse_args([
            "ai-macro", "scan", "--base-url", "https://testnet.binance.com"
        ])
        self.assertEqual(args.base_url, "https://testnet.binance.com")

    def test_ai_macro_subcommand_required(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["ai-macro"])

    def test_performance_send_telegram(self) -> None:
        args = build_parser().parse_args(["ai-macro", "performance", "--send-telegram"])
        self.assertTrue(args.send_telegram)

    def test_log_level_default(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan"])
        self.assertEqual(args.log_level, "INFO")

    def test_log_level_custom(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan", "--log-level", "DEBUG"])
        self.assertEqual(args.log_level, "DEBUG")

    def test_review_send_telegram(self) -> None:
        args = build_parser().parse_args(["ai-macro", "review", "--send-telegram"])
        self.assertTrue(args.send_telegram)

    def test_settle_send_telegram(self) -> None:
        args = build_parser().parse_args(["ai-macro", "settle", "--send-telegram"])
        self.assertTrue(args.send_telegram)

    def test_scan_timeout_custom(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan", "--timeout", "30.0"])
        self.assertAlmostEqual(args.timeout, 30.0)

    def test_scan_max_retries_custom(self) -> None:
        args = build_parser().parse_args(["ai-macro", "scan", "--max-retries", "5"])
        self.assertEqual(args.max_retries, 5)


if __name__ == "__main__":
    unittest.main()
