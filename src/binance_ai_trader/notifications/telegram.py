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
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._url = f"{self._base_url}/sendMessage"
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

    def reply(self, chat_id: int | str, text: str) -> None:
        """Send a message to a specific chat_id (for command responses)."""
        message = text.strip()
        if not message:
            return
        for chunk in _chunks(message, 4096):
            body = urlencode({"chat_id": str(chat_id), "text": chunk}).encode()
            request = Request(self._url, data=body, method="POST")
            with self._opener(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram API rejected reply: {payload}")

    def get_updates(self, offset: int = 0, timeout: int = 5) -> list[dict]:
        """Poll for new updates (messages/commands). Returns list of update dicts."""
        url = f"{self._base_url}/getUpdates"
        body = urlencode({"offset": offset, "timeout": timeout, "limit": 10}).encode()
        request = Request(url, data=body, method="POST")
        try:
            with self._opener(request, timeout=float(timeout + 5)) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("ok"):
                return []
            return payload.get("result", [])
        except Exception:
            return []


def _chunks(text: str, limit: int) -> tuple[str, ...]:
    return tuple(text[index:index + limit] for index in range(0, len(text), limit))
