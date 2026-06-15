from __future__ import annotations

from binance_ai_trader.hotlist.models import HotlistDailySummary


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
