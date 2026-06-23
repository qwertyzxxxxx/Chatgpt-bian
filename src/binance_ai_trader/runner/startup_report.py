"""Startup Telegram report: sent once when the run-loop acquires its lock."""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import UTC, datetime

log = logging.getLogger(__name__)


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return out.decode().strip() or "unknown"
    except Exception:
        return "unknown"


def _git_branch() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return out.decode().strip() or "unknown"
    except Exception:
        return "unknown"


def _env_id() -> str:
    for var in ("REPL_ID", "REPLIT_CLUSTER", "HOSTNAME"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return "unknown"


def build_startup_message(
    db_path: str,
    enabled_modules: dict[str, bool],
) -> str:
    """Return the formatted startup Telegram message string."""
    now = datetime.now(UTC)
    sha = _git_sha()
    branch = _git_branch()
    pid = os.getpid()
    env = _env_id()

    module_labels = [
        ("hotlist_alert",      "Hotlist Alert"),
        ("gemini_committee",   "Gemini Committee"),
        ("performance_center", "Performance Center"),
        ("leaderboard_watch",  "Leaderboard Watch"),
        ("strategy_health",    "Strategy Health"),
        ("hourly_report",      "Hourly Strategy Report"),
    ]
    module_lines = [
        f"  {label}: {'ON' if enabled_modules.get(key) else 'OFF'}"
        for key, label in module_labels
    ]

    lines = [
        "🚀 Binance AI Trader Started",
        f"时间: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "📌 版本",
        f"  Git SHA:  {sha}",
        f"  Branch:   {branch}",
        f"  PID:      {pid}",
        "",
        "🗄 数据库",
        f"  {db_path}",
        "",
        "🌐 环境",
        f"  {env}",
        "",
        "⚙️ 已启用模块",
        *module_lines,
        "",
        "仅供研究 | 不进行实盘交易",
    ]
    return "\n".join(lines)


def send_startup_report(
    db_path: str,
    enabled_modules: dict[str, bool],
    bot_token: str,
    chat_id: str,
    timeout: float = 10.0,
) -> None:
    """Build and send the startup report; swallows errors to never block startup."""
    import json
    import urllib.request

    text = build_startup_message(db_path, enabled_modules)
    _MAX = 4096
    for chunk in [text[i: i + _MAX] for i in range(0, len(text), _MAX)]:
        payload = json.dumps({"chat_id": chat_id, "text": chunk}).encode()
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout):
                pass
        except Exception as exc:
            log.warning("Startup report send failed: %s", exc)
