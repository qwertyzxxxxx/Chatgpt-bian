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
            "disclaimer": "Paper research only; no profit is guaranteed and no orders are placed.",
        }
