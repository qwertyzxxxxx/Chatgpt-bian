from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TelegramResult:
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    enabled: bool
    bot_token: str | None
    chat_id: str | None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "TelegramConfig":
        values = os.environ if environ is None else environ
        enabled = values.get("TELEGRAM_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on",
        }
        return cls(
            enabled=enabled,
            bot_token=values.get("TELEGRAM_BOT_TOKEN") or None,
            chat_id=values.get("TELEGRAM_CHAT_ID") or None,
        )


class TelegramClient:
    """Small urllib-only Telegram sender with safe disabled/missing-env behavior."""

    def __init__(self, config: TelegramConfig, timeout: float = 10.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._config = config
        self._timeout = timeout

    @classmethod
    def from_env(cls, timeout: float = 10.0) -> "TelegramClient":
        return cls(TelegramConfig.from_env(), timeout)

    def send(self, text: str) -> TelegramResult:
        if not self._config.enabled:
            return self._skipped("TELEGRAM_ENABLED is false")
        if not self._config.bot_token or not self._config.chat_id:
            return self._skipped("Telegram token or chat id is missing")
        if not text.strip():
            return TelegramResult("FAILED", "message is empty")

        url = f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage"
        body = urlencode({"chat_id": self._config.chat_id, "text": text}).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("ok") is True:
                return TelegramResult("SENT", "message delivered")
            return TelegramResult("FAILED", str(payload.get("description", "Telegram rejected message")))
        except HTTPError as error:
            detail = f"HTTPError: status={error.code} reason={error.reason}"
            LOGGER.warning("Telegram send failed: %s", detail)
            return TelegramResult("FAILED", detail)
        except URLError as error:
            detail = f"URLError: {error.reason}"
            LOGGER.warning("Telegram send failed: %s", detail)
            return TelegramResult("FAILED", detail)
        except (TimeoutError, OSError, ValueError) as error:
            detail = f"{type(error).__name__}: {error}"
            LOGGER.warning("Telegram send failed: %s", detail)
            return TelegramResult("FAILED", detail)

    @staticmethod
    def _skipped(detail: str) -> TelegramResult:
        LOGGER.info("Telegram notification SKIPPED: %s", detail)
        return TelegramResult("SKIPPED", detail)


def format_daily_top3(report: Mapping[str, object]) -> str:
    signals = list(report.get("signals") or [])[:3]
    regime = report.get("regime") or {}
    combined = regime.get("combined_regime", "UNKNOWN") if isinstance(regime, dict) else "UNKNOWN"
    lines = [
        f"Binance AI Trader daily report {report.get('date', '')}",
        f"Regime: {combined}",
        "Top 3 signals:",
    ]
    if not signals:
        lines.append("No signals")
    for index, signal in enumerate(signals, start=1):
        if isinstance(signal, dict):
            lines.append(
                f"{index}. {signal.get('symbol')} {signal.get('direction')} "
                f"score={signal.get('score')} sector={signal.get('sector')}"
            )
    account = report.get("paper_account") or {}
    if isinstance(account, dict):
        lines.append(f"Paper equity: {account.get('equity', 'N/A')} USDT")
    lines.append("Research/paper only. No orders are placed.")
    return "\n".join(lines)
