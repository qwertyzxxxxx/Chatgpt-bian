from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramNotifier:
    """Small Telegram Bot API client with no third-party dependency."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout: float = 10.0,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        if not bot_token.strip() or not chat_id.strip():
            raise ValueError("Telegram bot token and chat ID are both required")
        if timeout <= 0:
            raise ValueError("Telegram timeout must be positive")
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout
        self._opener = opener

    def send(self, text: str) -> None:
        message = text.strip()
        if not message:
            return
        for chunk in _chunks(message, 4096):
            body = urlencode({"chat_id": self._chat_id, "text": chunk}).encode()
            request = Request(self._url, data=body, method="POST")
            with self._opener(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram API rejected notification: {payload}")


def _chunks(text: str, limit: int) -> tuple[str, ...]:
    return tuple(text[index:index + limit] for index in range(0, len(text), limit))
