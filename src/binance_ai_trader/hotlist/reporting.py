from __future__ import annotations

from collections.abc import Sequence

from binance_ai_trader.hotlist.models import HotlistAIReview, HotlistDailySummary


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
