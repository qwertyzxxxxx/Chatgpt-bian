from __future__ import annotations

import json
import urllib.request
import urllib.parse
import logging
from typing import List, Optional

from .models import StrategyStats, Leaderboard

log = logging.getLogger(__name__)

_STRATEGY_LABELS = {
    "hotlist": "Hotlist",
    "ai_macro": "AI Macro",
    "gemini_committee": "Gemini Committee",
}
_MAX_CHARS = 4096


def _label(strategy: str) -> str:
    return _STRATEGY_LABELS.get(strategy, strategy)


def format_summary(stats: List[StrategyStats], leaderboard: Leaderboard) -> str:
    lines = ["📊 Strategy Performance\n"]
    for s in stats:
        lines += [
            f"─── {_label(s.strategy)} ───",
            f"Trades: {s.total} | Open: {s.open_count}",
            f"TP1: {s.tp1} | TP2: {s.tp2} | SL: {s.sl}",
            f"Win Rate: {s.win_rate}%",
            f"Avg RR: {s.avg_rr}",
            "",
        ]
    if leaderboard.entries:
        top = leaderboard.entries[0]
        lines += [
            f"🏆 当前第一名：",
            f"{_label(top.strategy)}",
            f"Win Rate {top.win_rate}% | Trades {top.total}",
        ]
    lines.append("\nResearch Only | No live trading")
    return "\n".join(lines)


def format_leaderboard(leaderboard: Leaderboard) -> str:
    lines = ["🏆 Strategy Leaderboard\n"]
    for i, s in enumerate(leaderboard.entries, 1):
        lines += [
            f"{i}. {_label(s.strategy)}",
            f"   Win Rate {s.win_rate}%  |  Trades {s.total}  |  Avg RR {s.avg_rr}",
            "",
        ]
    if not leaderboard.entries:
        lines.append("No data yet.")
    lines.append("Research Only | No live trading")
    return "\n".join(lines)


def _send(text: str, bot_token: str, chat_id: str, timeout: int = 10) -> bool:
    chunks = [text[i:i + _MAX_CHARS] for i in range(0, len(text), _MAX_CHARS)]
    ok = True
    for chunk in chunks:
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
            log.warning("Telegram send failed: %s", exc)
            ok = False
    return ok


def send_summary(
    stats: List[StrategyStats],
    leaderboard: Leaderboard,
    bot_token: str,
    chat_id: str,
    timeout: int = 10,
) -> bool:
    text = format_summary(stats, leaderboard)
    return _send(text, bot_token, chat_id, timeout)
