import json
import unittest
from urllib.parse import parse_qs

from binance_ai_trader.notifications import TelegramNotifier


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps({"ok": True}).encode()


class TelegramNotifierTest(unittest.TestCase):
    def test_send_posts_chat_and_message(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return _Response()

        TelegramNotifier("token", "chat-1", timeout=4.0, opener=opener).send("hello")

        self.assertEqual(1, len(requests))
        request, timeout = requests[0]
        self.assertEqual("https://api.telegram.org/bottoken/sendMessage", request.full_url)
        self.assertEqual(4.0, timeout)
        self.assertEqual(
            {"chat_id": ["chat-1"], "text": ["hello"]},
            parse_qs(request.data.decode()),
        )

    def test_long_messages_are_split_at_telegram_limit(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return _Response()

        TelegramNotifier("token", "chat", opener=opener).send("x" * 4097)

        self.assertEqual([4096, 1], [len(parse_qs(item.data.decode())["text"][0]) for item in requests])


if __name__ == "__main__":
    unittest.main()
