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
        return "Hotlist 观察站 V1：暂无合格研究候选。"
    lines = ["Hotlist 观察站 V1 — 仅供研究"]
    for plan in plans:
        lines.extend(
            [
                "",
                f"{plan.symbol} {plan.direction} ({plan.change_24h_pct:+}%)",
                f"买入 {plan.suggested_limit_entry} | 止损 {plan.stop_loss}",
                f"TP1 {plan.tp1} | TP2 {plan.tp2} | RR {plan.rr}",
                f"到期 {plan.expires_at}",
            ]
        )
    return "\n".join(lines)


def format_hotlist_alert_batch_message(
    alerts: Sequence[HotlistAlert], max_n: int = 3
) -> str:
    """Merge up to max_n alerts into one Telegram message (Rule 5)."""
    top = list(alerts[:max_n])
    if not top:
        return ""
    lines = [f"🔥 Hotlist Alert Top{len(top)}", ""]
    for i, alert in enumerate(top, 1):
        plan = alert.plan
        lines += [
            f"{i}. {alert.symbol} {alert.direction}",
            f"   买入: {plan.suggested_limit_entry}",
            f"   止损: {plan.stop_loss}",
            f"   TP1: {plan.tp1}",
            f"   TP2: {plan.tp2}",
            f"   RR: {plan.rr}",
            "",
        ]
    lines.append("仅供研究")
    return "\n".join(lines)


def format_hotlist_alert_message(alert: HotlistAlert) -> str:
    plan = alert.plan
    return "\n".join(
        [
            "🔔 Hotlist 警报",
            "",
            plan.symbol,
            plan.direction,
            f"买入: {plan.suggested_limit_entry}",
            f"止损: {plan.stop_loss}",
            f"TP1: {plan.tp1}",
            f"TP2: {plan.tp2}",
            f"RR: {plan.rr}",
            f"到期: {plan.expires_at}",
            f"理由: {plan.reason}",
            "",
            "仅供研究",
        ]
    )


def format_hotlist_ai_review_message(reviews: Sequence[HotlistAIReview]) -> str:
    """Format the Top 5 review without sending a Telegram message."""
    lines = ["Hotlist AI 复盘 V2", "", "仅供研究"]
    for review in reviews[:5]:
        lines.extend(
            [
                "",
                f"{review.symbol} {review.direction} — {review.confidence}",
                f"买入: {review.entry} | 止损: {review.stop_loss}",
                f"TP1: {review.tp1} | TP2: {review.tp2} | RR: {review.rr}",
                f"到期: {review.expires_at}",
                f"理由: {review.reason}",
            ]
        )
    return "\n".join(lines)


def format_hotlist_performance_summary(
    statistics: HotlistPerformanceStatistics,
) -> str:
    """Format aggregate research performance without sending it."""
    return "\n".join(
        [
            "Hotlist 绩效报告 V1",
            "",
            f"机会总数: {statistics.total_opportunities}",
            f"胜率: {statistics.win_rate:.2f}%",
            f"TP1率: {statistics.tp1_rate:.2f}%",
            f"TP2率: {statistics.tp2_rate:.2f}%",
            f"平均RR: {statistics.average_rr:.2f}",
            f"平均收益: {statistics.average_return:.2f}%",
            "",
            "仅供研究",
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
    "watchlist_active": "监控列表活跃",
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

    reason_counts: dict[str, int] = {}
    for r in report.top_rejections:
        reason_counts[r.reason] = reason_counts.get(r.reason, 0) + 1
    if reason_counts:
        lines += ["", "── 主要淘汰原因 ──"]
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {reason}: {count}")

    if report.top_rejections:
        lines += ["", "── 前10被淘汰币种 ──"]
        for r in report.top_rejections:
            lines.append(f"  {r.symbol} — {r.reason} ({r.detail})")

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
