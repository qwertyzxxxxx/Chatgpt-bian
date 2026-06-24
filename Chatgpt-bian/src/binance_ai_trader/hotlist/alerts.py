from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from binance_ai_trader.hotlist.models import (
    HotlistAlert,
    HotlistDailySummary,
    HotlistEntryPlan,
    SkippedAlert,
)
from binance_ai_trader.hotlist.repository import HotlistWatchlistRepository


class OpportunityReview(Protocol):
    def review(self, now: datetime | None = None) -> tuple[HotlistEntryPlan, ...]: ...


class HotlistAlertEngine:
    """Generate deduplicated research alerts without sending or trading.

    Deduplication rules (applied in order):
      1. duplicate_open_symbol  — non-expired hotlist_opportunities row for this symbol
      2. opposite_direction_open — open opportunity exists but in the opposite direction
      3. cooldown_active         — any alert for this symbol within cooldown_hours
      4. missing_plan            — plan fails quality gate (RR / stop-pct / volume)
    """

    def __init__(
        self,
        review: OpportunityReview,
        repository: HotlistWatchlistRepository,
        cooldown_hours: int = 4,
    ) -> None:
        if cooldown_hours < 0:
            raise ValueError("cooldown_hours must be non-negative")
        self._review = review
        self._repository = repository
        self._cooldown_hours = cooldown_hours

    def generate(
        self, now: datetime | None = None
    ) -> tuple[tuple[HotlistAlert, ...], tuple[SkippedAlert, ...], HotlistDailySummary]:
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        plans = self._review.review(generated_at)
        alerts: list[HotlistAlert] = []
        skipped: list[SkippedAlert] = []

        now_iso = generated_at.isoformat(timespec="seconds")
        cooldown_cutoff = (
            generated_at - timedelta(hours=self._cooldown_hours)
        ).isoformat(timespec="seconds")
        created_at = now_iso

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
                skipped.append(SkippedAlert(plan.symbol, plan.direction, "missing_plan"))
                continue

            open_dir = self._repository.open_opportunity_direction(plan.symbol, now_iso)
            if open_dir is not None:
                if open_dir != plan.direction:
                    skipped.append(
                        SkippedAlert(plan.symbol, plan.direction, "opposite_direction_open")
                    )
                else:
                    skipped.append(
                        SkippedAlert(plan.symbol, plan.direction, "duplicate_open_symbol")
                    )
                continue

            if self._repository.has_open_opportunity(plan.symbol, now_iso):
                skipped.append(
                    SkippedAlert(plan.symbol, plan.direction, "duplicate_open_symbol")
                )
                continue

            if self._repository.has_recent_alert_cooldown(plan.symbol, cooldown_cutoff):
                skipped.append(
                    SkippedAlert(plan.symbol, plan.direction, "cooldown_active")
                )
                continue

            rank_type = watchlist_item.source if watchlist_item else "UNKNOWN"
            alert = HotlistAlert(
                symbol=plan.symbol,
                direction=plan.direction,
                entry=plan.suggested_limit_entry,
                created_at=created_at,
                level=alert_level(plan.rr),
                plan=plan,
                rank_type=rank_type,
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
        return tuple(alerts), tuple(skipped), summary


def alert_level(rr: Decimal) -> str:
    if rr >= Decimal("3"):
        return "HIGH"
    if rr >= Decimal("2"):
        return "MEDIUM"
    return "LOW"
