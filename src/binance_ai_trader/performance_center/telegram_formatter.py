from __future__ import annotations

import json
import urllib.request
import urllib.parse
import logging
from typing import List, Optional

from .models import StrategyStats, Leaderboard

log = logging.getLogger(__name__)

_STRATEGY_LABELS = {
    "baseline_v1": "综合基准",
    "breakout_hunter_v1": "突破猎手",
    "bear_short_space80_v1": "熊市空头",
    "capital_60_80_space80_v1": "资金+空间",
    "range_disabled_v1": "趋势优先",
    "hotlist": "热门榜单（Hotlist）",
    "ai_macro": "AI宏观",
    "gemini_committee": "Gemini AI委员会",
}
_MAX_CHARS = 4096


def _label(strategy: str) -> str:
    return _STRATEGY_LABELS.get(strategy, strategy)


def format_summary(stats: List[StrategyStats], leaderboard: Leaderboard) -> str:
    lines = ["📊 策略绩效\n"]
    for s in stats:
        lines += [
            f"─── {_label(s.strategy)} ───",
            f"交易数: {s.total} | 持仓中: {s.open_count}",
            f"TP1: {s.tp1} | TP2: {s.tp2} | SL: {s.sl}",
            f"胜率: {s.win_rate}%",
            f"平均RR: {s.avg_rr}",
            "",
        ]
    if leaderboard.entries:
        top = leaderboard.entries[0]
        lines += [
            f"🏆 当前第一名：",
            f"{_label(top.strategy)}",
            f"胜率 {top.win_rate}% | 交易数 {top.total}",
        ]
    lines.append("\n仅供研究 | 不进行实盘交易")
    return "\n".join(lines)


def format_leaderboard(leaderboard: Leaderboard) -> str:
    lines = ["🏆 策略排行榜\n"]
    for i, s in enumerate(leaderboard.entries, 1):
        lines += [
            f"{i}. {_label(s.strategy)}",
            f"   胜率 {s.win_rate}%  |  交易数 {s.total}  |  平均RR {s.avg_rr}",
            "",
        ]
    if not leaderboard.entries:
        lines.append("暂无数据。")
    lines.append("仅供研究 | 不进行实盘交易")
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
