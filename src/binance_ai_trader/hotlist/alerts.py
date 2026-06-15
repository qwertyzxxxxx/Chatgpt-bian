from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from binance_ai_trader.hotlist.models import (
    HotlistAlert,
    HotlistDailySummary,
    HotlistEntryPlan,
)
from binance_ai_trader.hotlist.repository import HotlistWatchlistRepository


class OpportunityReview(Protocol):
    def review(self, now: datetime | None = None) -> tuple[HotlistEntryPlan, ...]: ...


class HotlistAlertEngine:
    """Generate deduplicated research alerts without sending or trading."""

    def __init__(
        self,
        review: OpportunityReview,
        repository: HotlistWatchlistRepository,
        dedup_minutes: int = 60,
    ) -> None:
        if dedup_minutes < 1:
            raise ValueError("dedup_minutes must be positive")
        self._review = review
        self._repository = repository
        self._dedup_minutes = dedup_minutes

    def generate(
        self, now: datetime | None = None
    ) -> tuple[tuple[HotlistAlert, ...], HotlistDailySummary]:
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        plans = self._review.review(generated_at)
        alerts = []
        cutoff = (generated_at - timedelta(minutes=self._dedup_minutes)).isoformat(
            timespec="seconds"
        )
        created_at = generated_at.isoformat(timespec="seconds")
        for plan in plans:
            watchlist_item = self._repository.load(plan.symbol)
            if watchlist_item is None or watchlist_item.status != "ACTIVE":
                continue
            stop_pct = (
                abs(plan.suggested_limit_entry - plan.stop_loss)
                / plan.suggested_limit_entry
                * Decimal("100")
            )
            if (
                plan.rr < Decimal("2")
                or stop_pct > Decimal("5")
                or plan.quote_volume < Decimal("5000000")
            ):
                continue
            if self._repository.has_recent_alert(
                plan.symbol,
                plan.direction,
                str(plan.suggested_limit_entry),
                cutoff,
            ):
                continue
            alert = HotlistAlert(
                symbol=plan.symbol,
                direction=plan.direction,
                entry=plan.suggested_limit_entry,
                created_at=created_at,
                level=alert_level(plan.rr),
                plan=plan,
            )
            self._repository.save_alert(alert)
            alerts.append(alert)
        watchlist = self._repository.all()
        summary = HotlistDailySummary(
            generated_at=created_at,
            symbols_watched=len(watchlist),
            alerts_generated=len(alerts),
            expired_symbols=sum(item.status == "EXPIRED" for item in watchlist),
            top_opportunities=plans[:3],
        )
        return tuple(alerts), summary


def alert_level(rr: Decimal) -> str:
    if rr >= Decimal("3"):
        return "HIGH"
    if rr >= Decimal("2"):
        return "MEDIUM"
    return "LOW"
