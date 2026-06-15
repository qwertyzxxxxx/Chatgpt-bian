from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from binance_ai_trader.hotlist.funnel import HotlistFunnelReport

from binance_ai_trader.hotlist.models import (
    HotlistAIReview,
    HotlistAlert,
    HotlistEntryPlan,
    HotlistPerformanceStatistics,
)


def format_hotlist_message(plans: Sequence[HotlistEntryPlan]) -> str:
    """Format research plans without sending or changing Telegram behavior."""
    if not plans:
        return "Hotlist Watcher V1: no qualifying research candidates."
    lines = ["Hotlist Watcher V1 — RESEARCH ONLY"]
    for plan in plans:
        lines.extend(
            [
                "",
                f"{plan.symbol} {plan.direction} ({plan.change_24h_pct:+}%)",
                f"Entry {plan.suggested_limit_entry} | SL {plan.stop_loss}",
                f"TP1 {plan.tp1} | TP2 {plan.tp2} | RR {plan.rr}",
                f"Expires {plan.expires_at}",
            ]
        )
    return "\n".join(lines)


def format_hotlist_alert_message(alert: HotlistAlert) -> str:
    plan = alert.plan
    return "\n".join(
        [
            "HOTLIST ALERT",
            "",
            plan.symbol,
            plan.direction,
            f"entry: {plan.suggested_limit_entry}",
            f"SL: {plan.stop_loss}",
            f"TP1: {plan.tp1}",
            f"TP2: {plan.tp2}",
            f"RR: {plan.rr}",
            f"expiry: {plan.expires_at}",
            f"reason: {plan.reason}",
            "",
            "Research only",
        ]
    )


def format_hotlist_ai_review_message(reviews: Sequence[HotlistAIReview]) -> str:
    """Format the Top 5 review without sending a Telegram message."""
    lines = ["HOTLIST AI REVIEW V2", "", "Research only"]
    for review in reviews[:5]:
        lines.extend(
            [
                "",
                f"{review.symbol} {review.direction} — {review.confidence}",
                f"entry: {review.entry} | SL: {review.stop_loss}",
                f"TP1: {review.tp1} | TP2: {review.tp2} | RR: {review.rr}",
                f"expiry: {review.expires_at}",
                f"reason: {review.reason}",
            ]
        )
    return "\n".join(lines)


def format_hotlist_performance_summary(
    statistics: HotlistPerformanceStatistics,
) -> str:
    """Format aggregate research performance without sending it."""
    return "\n".join(
        [
            "HOTLIST PERFORMANCE V1",
            "",
            f"Opportunities: {statistics.total_opportunities}",
            f"Win rate: {statistics.win_rate:.2f}%",
            f"TP1 rate: {statistics.tp1_rate:.2f}%",
            f"TP2 rate: {statistics.tp2_rate:.2f}%",
            f"Average RR: {statistics.average_rr:.2f}",
            f"Average return: {statistics.average_return:.2f}%",
            "",
            "Research only",
        ]
    )


_STEP_LABELS: dict[str, str] = {
    "universe_total": "合约总数",
    "usdt_perpetual": "USDT永续",
    "after_exclusions": "排除后",
    "move_ge_min_move": "涨跌幅通过",
    "volume_ge_min_quote_volume": "成交量通过",
    "gainers": "  上涨",
    "losers": "  下跌",
    "watchlist_active": "Watchlist ACTIVE",
    "review_candidates": "候选计划",
    "rr_pass": "RR通过",
    "stop_pass": "止损通过",
    "final_opportunities": "最终机会",
}


def format_hotlist_funnel_message(report: "HotlistFunnelReport") -> str:
    """Format funnel diagnostic report as a Telegram-friendly text message."""
    params = report.parameters
    lines = [
        "📊 Hotlist 漏斗报告",
        "",
        f"时间（UTC）: {report.generated_at}",
        f"参数: 涨跌≥{params.get('min_move_pct')}%  量≥{params.get('min_quote_volume')}  RR≥{params.get('min_rr')}  止损≤{params.get('max_stop_pct')}%",
        "",
        "── 漏斗各层 ──",
    ]
    for step in report.steps:
        label = _STEP_LABELS.get(step.label, step.label)
        if step.dropped == 0:
            lines.append(f"{label}: {step.count}")
        else:
            lines.append(
                f"{label}: {step.count}  (↓{step.dropped}, -{step.drop_off_pct:.1f}%)"
            )

    # Top rejection reasons summary
    reason_counts: dict[str, int] = {}
    for r in report.top_rejections:
        reason_counts[r.reason] = reason_counts.get(r.reason, 0) + 1
    if reason_counts:
        lines += ["", "── 主要淘汰原因 ──"]
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {reason}: {count}")

    # Top rejections detail
    if report.top_rejections:
        lines += ["", "── 前10被淘汰币种 ──"]
        for r in report.top_rejections:
            lines.append(f"  {r.symbol} — {r.reason} ({r.detail})")

    # Final opportunities with trade plan details
    lines += ["", "── 最终机会 ──"]
    if report.final_opportunities:
        for opp in report.final_opportunities:
            lines += [
                "",
                f"🔥 {opp.symbol}",
                f"方向: {opp.direction}",
                f"买入: {opp.entry}",
                f"止损: {opp.stop_loss}",
                f"TP1: {opp.tp1}",
                f"TP2: {opp.tp2}",
                f"RR: {opp.rr}",
            ]
    else:
        lines.append("  （无机会）")

    lines += ["", "Research Only — 仅供研究"]
    return "\n".join(lines)
