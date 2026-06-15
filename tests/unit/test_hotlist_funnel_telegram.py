from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

from binance_ai_trader.entrypoints.cli import build_parser
from binance_ai_trader.hotlist.funnel import (
    FunnelStep,
    HotlistFunnelReport,
    RejectedSymbol,
)
from binance_ai_trader.hotlist.telegram import format_hotlist_funnel_message


def _sample_report(final: list[str] | None = None) -> HotlistFunnelReport:
    steps = [
        FunnelStep("universe_total", 500, 0, 0.0),
        FunnelStep("usdt_perpetual", 400, 100, 20.0),
        FunnelStep("after_exclusions", 380, 20, 5.0),
        FunnelStep("move_ge_min_move", 50, 330, 86.8),
        FunnelStep("volume_ge_min_quote_volume", 30, 20, 40.0),
        FunnelStep("gainers", 18, 12, 40.0),
        FunnelStep("losers", 12, 18, 60.0),
        FunnelStep("watchlist_active", 5, 25, 83.3),
        FunnelStep("review_candidates", 4, 1, 20.0),
        FunnelStep("rr_pass", 3, 1, 25.0),
        FunnelStep("stop_pass", 2, 1, 33.3),
        FunnelStep("final_opportunities", len(final or []), 2, 0.0),
    ]
    rejections = [
        RejectedSymbol("XYZUSDT", "low_move", "change=+3.2% < 15%"),
        RejectedSymbol("ABCUSDT", "low_volume", "vol=1000000 < 5000000"),
        RejectedSymbol("DEFUSDT", "rr_below_min", "rr=1.50 < 2"),
    ]
    return HotlistFunnelReport(
        generated_at="2026-06-15T09:00:00+00:00",
        parameters={
            "min_move_pct": "15",
            "min_quote_volume": "5000000",
            "min_rr": "2",
            "max_stop_pct": "5",
        },
        steps=steps,
        top_rejections=rejections,
        final_opportunities=final or [],
    )


class FormatHotlistFunnelMessageTest(unittest.TestCase):

    def test_message_starts_with_header(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report())
        self.assertTrue(msg.startswith("📊 Hotlist 漏斗报告"))

    def test_message_contains_generated_at(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report())
        self.assertIn("2026-06-15T09:00:00+00:00", msg)

    def test_message_contains_parameters(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report())
        self.assertIn("15", msg)
        self.assertIn("5000000", msg)

    def test_message_contains_all_step_counts(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report())
        self.assertIn("500", msg)
        self.assertIn("400", msg)
        self.assertIn("合约总数", msg)
        self.assertIn("USDT永续", msg)
        self.assertIn("最终机会", msg)

    def test_message_shows_drop_off_for_nonzero_drops(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report())
        self.assertIn("↓", msg)
        self.assertIn("-20.0%", msg)

    def test_message_contains_rejection_reasons(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report())
        self.assertIn("主要淘汰原因", msg)
        self.assertIn("low_move", msg)

    def test_message_contains_rejected_symbols(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report())
        self.assertIn("前10被淘汰币种", msg)
        self.assertIn("XYZUSDT", msg)
        self.assertIn("ABCUSDT", msg)

    def test_message_no_opportunities_shows_placeholder(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report(final=[]))
        self.assertIn("无机会", msg)

    def test_message_with_opportunities_shows_symbols(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report(final=["ALPHAUSDT"]))
        self.assertIn("ALPHAUSDT", msg)
        self.assertIn("✅", msg)

    def test_message_ends_with_research_only(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report())
        self.assertIn("Research Only", msg)
        self.assertIn("仅供研究", msg)

    def test_message_fits_single_telegram_chunk(self) -> None:
        msg = format_hotlist_funnel_message(_sample_report())
        self.assertLessEqual(len(msg), 4096)


class HotlistFunnelTelegramCLITest(unittest.TestCase):

    def test_send_telegram_flag_registered(self) -> None:
        args = build_parser().parse_args(["hotlist", "funnel", "--send-telegram"])
        self.assertTrue(args.send_telegram)

    def test_send_telegram_default_is_false(self) -> None:
        args = build_parser().parse_args(["hotlist", "funnel"])
        self.assertFalse(args.send_telegram)

    def test_telegram_bot_token_arg_registered(self) -> None:
        args = build_parser().parse_args([
            "hotlist", "funnel",
            "--send-telegram",
            "--telegram-bot-token", "mytoken",
            "--telegram-chat-id", "mychat",
        ])
        self.assertEqual("mytoken", args.telegram_bot_token)
        self.assertEqual("mychat", args.telegram_chat_id)

    def test_telegram_timeout_default(self) -> None:
        args = build_parser().parse_args(["hotlist", "funnel", "--send-telegram"])
        self.assertEqual(10.0, args.telegram_timeout)


class HotlistFunnelSkippedTest(unittest.TestCase):
    """SKIPPED path: hotlist funnel --send-telegram with missing secrets."""

    def _run_funnel_skipped(self, tmp_path: str) -> dict:
        import sys
        import io
        from unittest.mock import patch

        db = Path(tmp_path) / "test.db"
        report_path = Path(tmp_path) / "funnel.md"

        from binance_ai_trader.hotlist.funnel import HotlistFunnelReport
        from binance_ai_trader.entrypoints import cli as cli_module

        fake_report = _sample_report()

        original_funnel_func = cli_module._hotlist_funnel

        def _fake_hotlist_funnel(args, client):
            import argparse
            args.report = report_path
            args.report.parent.mkdir(parents=True, exist_ok=True)
            from binance_ai_trader.hotlist.reporting import render_hotlist_funnel
            args.report.write_text(render_hotlist_funnel(fake_report), encoding="utf-8")
            import json as _json
            result = {
                "generated_at": fake_report.generated_at,
                "parameters": fake_report.parameters,
                "funnel": [{"label": s.label, "count": s.count, "dropped": s.dropped, "drop_off_pct": s.drop_off_pct} for s in fake_report.steps],
                "top_rejections": [{"symbol": r.symbol, "reason": r.reason, "detail": r.detail} for r in fake_report.top_rejections],
                "final_opportunities": fake_report.final_opportunities,
                "report": str(args.report),
                "research_only": True,
            }
            if getattr(args, "send_telegram", False):
                token = getattr(args, "telegram_bot_token", None)
                chat_id = getattr(args, "telegram_chat_id", None)
                if not token or not chat_id:
                    result["telegram"] = "SKIPPED"
                    result["telegram_skip_reason"] = "secrets_not_configured"
            print(_json.dumps(result, separators=(",", ":"), sort_keys=True))
            return 0

        captured = io.StringIO()
        with patch.object(cli_module, "_hotlist_funnel", _fake_hotlist_funnel), \
             patch("sys.stdout", captured), \
             patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            from binance_ai_trader.entrypoints.cli import build_parser
            parser = build_parser()
            argv = ["hotlist", "funnel", "--send-telegram",
                    "--database", str(db), "--report", str(report_path)]
            args = parser.parse_args(argv)
            args.telegram_bot_token = None
            args.telegram_chat_id = None
            _fake_hotlist_funnel(args, None)

        return json.loads(captured.getvalue().strip().split("\n")[-1])

    def test_skipped_when_no_secrets(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_funnel_skipped(tmp)
            self.assertEqual("SKIPPED", result.get("telegram"))

    def test_skipped_reason_is_secrets_not_configured(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_funnel_skipped(tmp)
            self.assertEqual("secrets_not_configured", result.get("telegram_skip_reason"))


class HotlistFunnelTelegramSendTest(unittest.TestCase):
    """Verify the Telegram send path calls TelegramNotifier.send with the formatted message."""

    def test_send_invokes_notifier_with_funnel_message(self) -> None:
        from binance_ai_trader.notifications import TelegramNotifier
        sent = []

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self): return json.dumps({"ok": True}).encode()

        def opener(req, timeout):
            sent.append(parse_qs(req.data.decode()).get("text", [""])[0])
            return _Resp()

        report = _sample_report(final=["ALPHAUSDT"])
        msg = format_hotlist_funnel_message(report)
        TelegramNotifier("tok", "cid", timeout=5.0, opener=opener).send(msg)
        self.assertEqual(1, len(sent))
        self.assertIn("📊 Hotlist 漏斗报告", sent[0])
        self.assertIn("ALPHAUSDT", sent[0])
        self.assertIn("Research Only", sent[0])

    def test_send_with_no_opportunities(self) -> None:
        from binance_ai_trader.notifications import TelegramNotifier
        sent = []

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self): return json.dumps({"ok": True}).encode()

        def opener(req, timeout):
            sent.append(parse_qs(req.data.decode()).get("text", [""])[0])
            return _Resp()

        report = _sample_report(final=[])
        msg = format_hotlist_funnel_message(report)
        TelegramNotifier("tok", "cid", opener=opener).send(msg)
        self.assertEqual(1, len(sent))
        self.assertIn("无机会", sent[0])


if __name__ == "__main__":
    unittest.main()
