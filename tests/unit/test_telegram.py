import json
import unittest
from unittest.mock import patch

from binance_ai_trader.notifications import (
    TelegramClient,
    TelegramConfig,
    format_daily_top3,
)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps({"ok": True}).encode()


class TelegramClientTest(unittest.TestCase):
    def test_disabled_or_missing_environment_is_safely_skipped(self) -> None:
        disabled = TelegramClient(TelegramConfig(False, None, None)).send("test")
        missing = TelegramClient(TelegramConfig(True, None, None)).send("test")
        self.assertEqual("SKIPPED", disabled.status)
        self.assertEqual("SKIPPED", missing.status)

    @patch("binance_ai_trader.notifications.telegram.urlopen", return_value=_Response())
    def test_send_uses_telegram_http_api(self, mocked_urlopen) -> None:
        result = TelegramClient(TelegramConfig(True, "token", "chat"), timeout=4).send("hello")
        self.assertEqual("SENT", result.status)
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual("https://api.telegram.org/bottoken/sendMessage", request.full_url)
        self.assertIn(b"chat_id=chat", request.data)
        self.assertIn(b"text=hello", request.data)
        self.assertEqual(4, mocked_urlopen.call_args.kwargs["timeout"])

    def test_daily_top3_formatter_limits_signal_count(self) -> None:
        report = {
            "date": "2026-06-11",
            "regime": {"combined_regime": "BULL"},
            "signals": [
                {"symbol": f"COIN{i}USDT", "direction": "LONG", "score": 90 - i, "sector": "LAYER1"}
                for i in range(5)
            ],
            "paper_account": {"equity": "1000"},
        }
        text = format_daily_top3(report)
        self.assertIn("COIN0USDT", text)
        self.assertIn("COIN2USDT", text)
        self.assertNotIn("COIN3USDT", text)
        self.assertIn("Research/paper only", text)


if __name__ == "__main__":
    unittest.main()
