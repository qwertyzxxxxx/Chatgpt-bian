from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List

from .models import StrategyStats, Leaderboard

_STRATEGY_LABELS = {
    "hotlist": "Hotlist",
    "ai_macro": "AI Macro",
    "gemini_committee": "Gemini Committee",
}


def _label(strategy: str) -> str:
    return _STRATEGY_LABELS.get(strategy, strategy)


def _stats_block(s: StrategyStats) -> str:
    return (
        f"### {_label(s.strategy)}\n"
        f"- Trades: {s.total}\n"
        f"- Open: {s.open_count}\n"
        f"- TP1: {s.tp1} | TP2: {s.tp2} | SL: {s.sl}"
        f" | Timeout: {s.timeout} | Expired: {s.expired}\n"
        f"- Win Rate: {s.win_rate}%  (decisive only: TP1+TP2 vs SL)\n"
        f"- Avg RR: {s.avg_rr}\n"
        f"- Avg PnL%: {s.avg_pnl_pct}%\n"
        f"- Max Consec Wins: {s.max_consecutive_wins}"
        f" | Max Consec Losses: {s.max_consecutive_losses}\n"
    )


def generate_summary_md(
    stats: List[StrategyStats],
    path: str = "reports/performance_summary.md",
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Strategy Performance Summary\n\n_Generated: {now}_\n"]
    for s in stats:
        lines.append(_stats_block(s))
    content = "\n".join(lines)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return content


def generate_leaderboard_md(
    lb: Leaderboard,
    path: str = "reports/performance_leaderboard.md",
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Strategy Leaderboard\n\n_Generated: {now}_\n",
        "| Rank | Strategy | Win Rate | Trades | Avg RR |",
        "|------|----------|----------|--------|--------|",
    ]
    for i, s in enumerate(lb.entries, 1):
        lines.append(
            f"| {i} | {_label(s.strategy)} | {s.win_rate}% | {s.total} | {s.avg_rr} |"
        )
    if not lb.entries:
        lines.append("| — | No data | — | — | — |")
    content = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return content
