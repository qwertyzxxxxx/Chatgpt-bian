from __future__ import annotations

from datetime import UTC, date, datetime

from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


class DailyReportService:
    def __init__(self, repository: MarketDataRepository) -> None:
        self._repository = repository

    def build(self, report_date: date | None = None) -> dict[str, object]:
        selected = report_date or datetime.now(UTC).date()
        start = datetime.combine(selected, datetime.min.time(), UTC).isoformat(timespec="milliseconds")
        end = datetime.combine(selected, datetime.max.time(), UTC).isoformat(timespec="milliseconds")
        account = self._repository.load_or_create_paper_account(
            initial_equity=1000, updated_at=datetime.now(UTC).isoformat(timespec="milliseconds")
        )
        return {
            "date": selected.isoformat(),
            "signals": self._repository.load_signal_report(start, end),
            "top3": self._repository.load_daily_top_signals(start, end),
            "regime": self._repository.load_regime_report(start, end),
            "sectors": self._repository.load_sector_report(start, end),
            "paper_account": {
                "equity": str(account.equity),
                "mode": account.mode,
                "consecutive_losses": account.consecutive_losses,
                "paused_until": account.paused_until,
                "current_target": account.current_target,
            },
            "top_capital_long": self._repository.load_top_capital_signals(start, end, "LONG"),
            "top_capital_short": self._repository.load_top_capital_signals(start, end, "SHORT"),
            "top_candidates": self._repository.load_top_candidate_report(5),
            "aggressive_allowed": account.aggressive_allowed,
            "disclaimer": "仅供研究，不下单，不保证盈利。",
        }


_STRATEGY_LABELS: dict[str, str] = {
    "baseline_v1": "综合基准",
    "breakout_hunter_v1": "突破猎手",
    "bear_short_space80_v1": "熊市空头",
    "capital_60_80_space80_v1": "资金+空间",
    "range_disabled_v1": "趋势优先",
}


def format_top3_message(report: dict[str, object]) -> str:
    lines = [f"📊 主流程策略 Top3 — {report['date']}"]
    top3 = report.get("top3", [])
    if not top3:
        lines.append("暂无信号。")
    else:
        for index, item in enumerate(top3, start=1):
            strategy_id = item.get("strategy_id", "baseline_v1")
            strategy_label = _STRATEGY_LABELS.get(strategy_id, strategy_id)
            direction_emoji = "📈" if item["direction"] == "LONG" else "📉"
            lines.append(
                f"{index}. {direction_emoji} {item['symbol']} {item['direction']}"
                f"  [{strategy_label}]"
            )
            lines.append(
                f"   分数 {item['score']} | 买入 {item['entry']}"
                f" | SL {item['sl']} | TP1 {item['tp1']} | TP2 {item['tp2']} | RR {item['rr']}"
            )
    lines.append("\n仅供研究，不下单。")
    return "\n".join(lines)
