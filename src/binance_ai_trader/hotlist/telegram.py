from __future__ import annotations

from collections.abc import Sequence

from binance_ai_trader.hotlist.models import (
    HotlistAIReview,
    HotlistAlert,
    HotlistEntryPlan,
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
