"""V2 Startup Telegram notification — always sent once when V2 tasks boot.

Message format:
  [V2] Started
  版本:          0.1.0
  Git SHA:       abc1234
  Branch:        main
  PID:           12345
  DB:            data/market_data.db
  V2 Hotlist:    ON
  Shadow Report: ON  (1h)
  Health Check:  ON  (6h)
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from binance_ai_trader.notifications import TelegramNotifier

log = logging.getLogger(__name__)

_UNKNOWN = "unknown"


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return _UNKNOWN


def _git_branch() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return _UNKNOWN


def _package_version() -> str:
    try:
        from importlib.metadata import version
        return version("binance-ai-trader")
    except Exception:
        pass
    try:
        import tomllib
        pyproject = Path(__file__).parents[5] / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        return data["project"]["version"]
    except Exception:
        return _UNKNOWN


def send_v2_startup(
    notifier: TelegramNotifier,
    db_path: Path | str,
    shadow_report_enabled: bool,
    health_check_enabled: bool,
    report_interval_hours: int = 1,
    health_interval_hours: int = 6,
) -> None:
    sha = _git_sha()
    branch = _git_branch()
    ver = _package_version()
    pid = os.getpid()

    shadow_line = (
        f"ON  ({report_interval_hours}h)" if shadow_report_enabled else "OFF"
    )
    health_line = (
        f"ON  ({health_interval_hours}h)" if health_check_enabled else "OFF"
    )

    msg = (
        "[V2] Started\n\n"
        f"版本:          {ver}\n"
        f"Git SHA:       {sha}\n"
        f"Branch:        {branch}\n"
        f"PID:           {pid}\n"
        f"DB:            {db_path}\n"
        f"V2 Hotlist:    ON\n"
        f"Shadow Report: {shadow_line}\n"
        f"Health Check:  {health_line}"
    )
    try:
        notifier.send(msg)
        log.info("[V2] startup notification sent (SHA=%s branch=%s)", sha, branch)
    except Exception as exc:
        log.warning("[V2] failed to send startup notification: %s", exc)
