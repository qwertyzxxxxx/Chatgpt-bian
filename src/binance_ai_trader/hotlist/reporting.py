from __future__ import annotations

from collections.abc import Sequence

from binance_ai_trader.hotlist.models import (
    HotlistAIReview,
    HotlistDailySummary,
    HotlistOutcome,
    HotlistPerformanceStatistics,
    TrackedHotlistOpportunity,
)


def render_hotlist_daily_summary(summary: HotlistDailySummary) -> str:
    lines = [
        "# Hotlist Daily Summary",
        "",
        f"- **Generated at:** {summary.generated_at}",
        f"- **Symbols watched:** {summary.symbols_watched}",
        f"- **Alerts generated:** {summary.alerts_generated}",
        f"- **Expired symbols:** {summary.expired_symbols}",
        "",
        "## Top Opportunities",
        "",
        "| Symbol | Direction | Entry | SL | TP1 | TP2 | RR | Expiry |",
        "| --- | :--- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for plan in summary.top_opportunities:
        lines.append(
            f"| `{plan.symbol}` | {plan.direction} | {plan.suggested_limit_entry} | "
            f"{plan.stop_loss} | {plan.tp1} | {plan.tp2} | {plan.rr} | "
            f"{plan.expires_at} |"
        )
    lines.extend(
        [
            "",
            "> Research only. No live trading action is performed by this report.",
            "",
        ]
    )
    return "\n".join(lines)


def render_hotlist_top5_review(
    reviews: Sequence[HotlistAIReview], generated_at: str
) -> str:
    lines = [
        "# Hotlist Top 5 Research Review",
        "",
        f"- **Generated at:** {generated_at}",
        f"- **Opportunities reviewed:** {len(reviews)}",
        "",
        "| Symbol | Direction | Entry | SL | TP1 | TP2 | RR | Confidence | Expiry |",
        "| --- | :--- | ---: | ---: | ---: | ---: | ---: | :--- | --- |",
    ]
    for review in reviews:
        lines.append(
            f"| `{review.symbol}` | {review.direction} | {review.entry} | "
            f"{review.stop_loss} | {review.tp1} | {review.tp2} | {review.rr} | "
            f"{review.confidence} | {review.expires_at} |"
        )
        lines.append(f"|  |  | **Reason:** {review.reason} |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "> Research only. This review is not live trading advice and performs no trades.",
            "",
        ]
    )
    return "\n".join(lines)


def render_hotlist_performance(
    statistics: HotlistPerformanceStatistics,
    opportunities: Sequence[TrackedHotlistOpportunity],
    outcomes: Sequence[HotlistOutcome],
    generated_at: str,
) -> str:
    latest = {}
    for outcome in outcomes:
        if (
            outcome.opportunity_id not in latest
            or outcome.horizon_hours > latest[outcome.opportunity_id].horizon_hours
        ):
            latest[outcome.opportunity_id] = outcome
    lines = [
        "# Hotlist Performance Tracker V1",
        "",
        f"- **Generated at:** {generated_at}",
        f"- **Total opportunities:** {statistics.total_opportunities}",
        f"- **Win rate:** {statistics.win_rate:.2f}%",
        f"- **TP1 rate:** {statistics.tp1_rate:.2f}%",
        f"- **TP2 rate:** {statistics.tp2_rate:.2f}%",
        f"- **Average RR:** {statistics.average_rr:.2f}",
        f"- **Average return:** {statistics.average_return:.2f}%",
        "",
        "## Performance by Confidence",
        "",
        "| Confidence | Opportunities | Win Rate | TP1 Rate | TP2 Rate | Avg Return |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(_slice_rows(statistics.confidence_performance))
    best = statistics.symbol_performance[:5]
    worst = tuple(reversed(statistics.symbol_performance[-5:]))
    lines.extend(["", "## Best Symbols", "", *_slice_rows(best)])
    lines.extend(["", "## Worst Symbols", "", *_slice_rows(worst)])
    lines.extend(
        [
            "",
            "## Last 50 Opportunities",
            "",
            "| Symbol | Direction | Entry | SL | TP1 | TP2 | RR | Confidence | Created | Expiry | Outcome |",
            "| --- | :--- | ---: | ---: | ---: | ---: | ---: | :--- | --- | --- | :--- |",
        ]
    )
    for opportunity in opportunities[:50]:
        outcome = latest.get(opportunity.id)
        status = outcome.status if outcome is not None else "OPEN"
        lines.append(
            f"| `{opportunity.symbol}` | {opportunity.direction} | {opportunity.entry} | "
            f"{opportunity.stop_loss} | {opportunity.tp1} | {opportunity.tp2} | "
            f"{opportunity.rr} | {opportunity.confidence} | {opportunity.created_at} | "
            f"{opportunity.expires_at} | {status} |"
        )
    lines.extend(
        [
            "",
            "> Research only. Public market data only. No live trading is performed.",
            "",
        ]
    )
    return "\n".join(lines)


def _slice_rows(slices) -> list[str]:
    return [
        f"| {item.label} | {item.opportunities} | {item.win_rate:.2f}% | "
        f"{item.tp1_rate:.2f}% | {item.tp2_rate:.2f}% | "
        f"{item.average_return:.2f}% |"
        for item in slices
    ]
