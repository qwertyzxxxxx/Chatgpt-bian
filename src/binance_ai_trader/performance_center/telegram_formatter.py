from __future__ import annotations

import json
import urllib.request
import urllib.parse
import logging
from datetime import UTC, datetime, timedelta
from typing import List, Optional

from .models import StrategyStats, Leaderboard
from .stats import compute_all_stats_windowed

log = logging.getLogger(__name__)

_STRATEGY_LABELS = {
    "baseline_v1": "Baseline V1",
    "breakout_hunter_v1": "Breakout Hunter",
    "bear_short_space80_v1": "Bear Short",
    "capital_60_80_space80_v1": "Capital 60-80",
    "range_disabled_v1": "Range Disabled",
    "hotlist": "Hotlist",
    "ai_macro": "AI Macro",
    "gemini_committee": "Gemini Committee",
    "leaderboard_watch": "Leaderboard Watch",
}
_MAX_CHARS = 4096

_ALL_ORDERED = (
    "hotlist",
    "baseline_v1",
    "breakout_hunter_v1",
    "bear_short_space80_v1",
    "capital_60_80_space80_v1",
    "range_disabled_v1",
    "ai_macro",
    "gemini_committee",
)


def _label(strategy: str) -> str:
    return _STRATEGY_LABELS.get(strategy, strategy)


def _stats_line(s: StrategyStats) -> str:
    wins = s.tp1 + s.tp2
    pnl_sign = "+" if s.avg_pnl_pct >= 0 else ""
    return (
        f"交易{s.total} 胜率{s.win_rate}% RR{s.avg_rr} 均收益{pnl_sign}{s.avg_pnl_pct:.2f}%"
    )


def format_summary(
    stats: List[StrategyStats],
    leaderboard: Leaderboard,
    all_results: list | None = None,
) -> str:
    now = datetime.now(UTC)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 策略绩效（{now_str}）\n"]

    stats_by_strategy = {s.strategy: s for s in stats}

    if all_results is not None:
        cutoff_7d = (now - timedelta(days=7)).isoformat(timespec="seconds")
        cutoff_24h = (now - timedelta(hours=24)).isoformat(timespec="seconds")

        strategies_in_order = [
            s for s in _ALL_ORDERED if s in stats_by_strategy or any(
                r.strategy == s for r in all_results
            )
        ]
        extra = sorted({
            r.strategy for r in all_results
            if r.strategy not in set(_ALL_ORDERED)
        })
        strategies_in_order += extra

        stats_7d = {
            s.strategy: s
            for s in compute_all_stats_windowed(all_results, strategies_in_order, cutoff_7d)
        }
        stats_24h = {
            s.strategy: s
            for s in compute_all_stats_windowed(all_results, strategies_in_order, cutoff_24h)
        }

        for strat in strategies_in_order:
            s_all = stats_by_strategy.get(strat)
            s_7d = stats_7d.get(strat)
            s_24h = stats_24h.get(strat)
            if s_all is None or (s_all.total == 0 and
                                  (s_7d is None or s_7d.total == 0) and
                                  (s_24h is None or s_24h.total == 0)):
                continue
            lines.append(f"─── {_label(strat)} ───")
            lines.append(f"全部: {_stats_line(s_all)}")
            if s_7d and s_7d.total > 0:
                lines.append(f"近7天: {_stats_line(s_7d)}")
            if s_24h and s_24h.total > 0:
                lines.append(f"近24h: {_stats_line(s_24h)}")
            lines.append(f"持仓中: {s_all.open_count}  止损: {s_all.sl}  TP1: {s_all.tp1}  TP2: {s_all.tp2}")
            lines.append("")
    else:
        for s in stats:
            closed = s.total - s.open_count
            pnl_sign = "+" if s.avg_pnl_pct >= 0 else ""
            lines += [
                f"─── {_label(s.strategy)} ───",
                f"交易数: {s.total} | 持仓中: {s.open_count} | 已结算: {closed}",
                f"✅ 获利: {s.tp1 + s.tp2}笔  (TP1: {s.tp1} / TP2: {s.tp2})",
                f"❌ 止损: {s.sl}笔" + (f"  ⏰ 超时: {s.timeout}笔" if s.timeout > 0 else ""),
                f"胜率: {s.win_rate}%  |  平均RR: {s.avg_rr}  |  平均盈亏: {pnl_sign}{s.avg_pnl_pct:.2f}%",
                "",
            ]

    if leaderboard.entries:
        top = leaderboard.entries[0]
        lines += [
            "🏆 当前第一名：",
            f"{_label(top.strategy)}",
            f"胜率 {top.win_rate}%  |  交易数 {top.total}  |  平均RR {top.avg_rr}",
        ]
        if len(leaderboard.entries) > 1:
            lines.append("")
            lines.append("📋 完整排名：")
            for i, e in enumerate(leaderboard.entries, 1):
                lines.append(
                    f"  {i}. {_label(e.strategy)}  胜率{e.win_rate}%  交易{e.total}笔  RR{e.avg_rr}"
                )

    lines.append("\n仅供研究 | 不进行实盘交易")
    return "\n".join(lines)


def format_leaderboard(leaderboard: Leaderboard) -> str:
    lines = ["🏆 策略排行榜\n"]
    for i, s in enumerate(leaderboard.entries, 1):
        pnl_sign = "+" if s.avg_pnl_pct >= 0 else ""
        lines += [
            f"{i}. {_label(s.strategy)}",
            f"   胜率 {s.win_rate}%  |  交易数 {s.total}  |  平均RR {s.avg_rr}  |  平均盈亏 {pnl_sign}{s.avg_pnl_pct:.2f}%",
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
    all_results: list | None = None,
) -> bool:
    text = format_summary(stats, leaderboard, all_results=all_results)
    return _send(text, bot_token, chat_id, timeout)
