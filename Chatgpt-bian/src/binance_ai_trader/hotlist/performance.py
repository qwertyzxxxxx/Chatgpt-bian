from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.hotlist.models import (
    HotlistAIReview,
    HotlistOutcome,
    HotlistPerformanceSlice,
    HotlistPerformanceStatistics,
    TrackedHotlistOpportunity,
)
from binance_ai_trader.hotlist.performance_repository import (
    HotlistPerformanceRepository,
)


HORIZONS = (1, 4, 24)


class PublicKlines(Protocol):
    def klines(
        self, symbol: str, interval: str, limit: int = 200
    ) -> tuple[Kline, ...]: ...


class HotlistPerformanceTracker:
    """Track and evaluate AI-review plans using public klines only."""

    def __init__(
        self,
        client: PublicKlines,
        repository: HotlistPerformanceRepository,
    ) -> None:
        self._client = client
        self._repository = repository

    def track(
        self,
        reviews: Sequence[HotlistAIReview],
        created_at: datetime | None = None,
    ) -> tuple[TrackedHotlistOpportunity, ...]:
        timestamp = (created_at or datetime.now(UTC)).astimezone(UTC).isoformat(
            timespec="seconds"
        )
        return tuple(
            self._repository.save_opportunity(
                TrackedHotlistOpportunity(
                    id=None,
                    symbol=review.symbol,
                    direction=review.direction,
                    entry=review.entry,
                    stop_loss=review.stop_loss,
                    tp1=review.tp1,
                    tp2=review.tp2,
                    rr=review.rr,
                    confidence=review.confidence,
                    created_at=timestamp,
                    expires_at=review.expires_at,
                    rank_type=review.rank_type,
                )
            )
            for review in reviews
        )

    def evaluate(self, now: datetime | None = None) -> tuple[HotlistOutcome, ...]:
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        outcomes = []
        for opportunity in self._repository.opportunities():
            created_at = datetime.fromisoformat(opportunity.created_at)
            elapsed = evaluated_at - created_at
            candles = self._client.klines(opportunity.symbol, "1h", limit=25)
            for horizon in HORIZONS:
                if elapsed < timedelta(hours=horizon):
                    continue
                outcome = evaluate_opportunity(
                    opportunity,
                    candles,
                    horizon,
                    evaluated_at,
                )
                self._repository.save_outcome(outcome)
                outcomes.append(outcome)
        return tuple(outcomes)

    def statistics(self) -> HotlistPerformanceStatistics:
        opportunities = self._repository.opportunities()
        latest = _latest_outcomes(self._repository.outcomes())
        rows = [
            (opportunity, latest.get(opportunity.id))
            for opportunity in opportunities
        ]
        return HotlistPerformanceStatistics(
            total_opportunities=len(opportunities),
            win_rate=_rate(rows, {"TP1_HIT", "TP2_HIT"}),
            tp1_rate=_rate(rows, {"TP1_HIT", "TP2_HIT"}),
            tp2_rate=_rate(rows, {"TP2_HIT"}),
            average_rr=_average([item.rr for item in opportunities]),
            average_return=_average(
                [outcome.return_pct for _, outcome in rows if outcome is not None]
            ),
            confidence_performance=_group_performance(
                rows, "confidence", ("STRONG", "MEDIUM", "WEAK")
            ),
            symbol_performance=_group_performance(rows, "symbol"),
        )


def evaluate_opportunity(
    opportunity: TrackedHotlistOpportunity,
    candles: Sequence[Kline],
    horizon_hours: int,
    evaluated_at: datetime,
) -> HotlistOutcome:
    created_at = datetime.fromisoformat(opportunity.created_at)
    cutoff_ms = int((created_at + timedelta(hours=horizon_hours)).timestamp() * 1000)
    created_ms = int(created_at.timestamp() * 1000)
    relevant = [
        candle
        for candle in candles
        if candle.close_time_ms > created_ms and candle.close_time_ms <= cutoff_ms
    ]
    status = "OPEN"
    exit_price = opportunity.entry
    tp1_hit = False
    for candle in relevant:
        if opportunity.direction == "LONG":
            if candle.low <= opportunity.stop_loss:
                status, exit_price = "SL_HIT", opportunity.stop_loss
                break
            if candle.high >= opportunity.tp2:
                status, exit_price = "TP2_HIT", opportunity.tp2
                break
            if candle.high >= opportunity.tp1:
                tp1_hit = True
        else:
            if candle.high >= opportunity.stop_loss:
                status, exit_price = "SL_HIT", opportunity.stop_loss
                break
            if candle.low <= opportunity.tp2:
                status, exit_price = "TP2_HIT", opportunity.tp2
                break
            if candle.low <= opportunity.tp1:
                tp1_hit = True
    if status == "OPEN" and tp1_hit:
        status, exit_price = "TP1_HIT", opportunity.tp1
    elif status == "OPEN" and relevant:
        exit_price = relevant[-1].close
        expiry = datetime.fromisoformat(opportunity.expires_at)
        if evaluated_at >= expiry:
            status = "EXPIRED"
    return HotlistOutcome(
        opportunity_id=opportunity.id or 0,
        horizon_hours=horizon_hours,
        status=status,
        evaluated_at=evaluated_at.isoformat(timespec="seconds"),
        return_pct=_return_pct(opportunity, exit_price),
    )


def _return_pct(
    opportunity: TrackedHotlistOpportunity, exit_price: Decimal
) -> Decimal:
    direction = Decimal("1") if opportunity.direction == "LONG" else Decimal("-1")
    return (
        (exit_price - opportunity.entry)
        / opportunity.entry
        * Decimal("100")
        * direction
    )


def _latest_outcomes(
    outcomes: Sequence[HotlistOutcome],
) -> dict[int, HotlistOutcome]:
    latest: dict[int, HotlistOutcome] = {}
    for outcome in outcomes:
        current = latest.get(outcome.opportunity_id)
        if current is None or outcome.horizon_hours > current.horizon_hours:
            latest[outcome.opportunity_id] = outcome
    return latest


def _rate(rows, statuses: set[str]) -> Decimal:
    evaluated = [outcome for _, outcome in rows if outcome is not None]
    if not evaluated:
        return Decimal("0")
    wins = sum(outcome.status in statuses for outcome in evaluated)
    return Decimal(wins) / Decimal(len(evaluated)) * Decimal("100")


def _average(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")


def _group_performance(
    rows, attribute: str, required_labels: Sequence[str] = ()
) -> tuple[HotlistPerformanceSlice, ...]:
    grouped = defaultdict(list)
    for opportunity, outcome in rows:
        grouped[getattr(opportunity, attribute)].append((opportunity, outcome))
    for label in required_labels:
        grouped[label]
    results = []
    for label, group in grouped.items():
        returns = [
            outcome.return_pct for _, outcome in group if outcome is not None
        ]
        results.append(
            HotlistPerformanceSlice(
                label=label,
                opportunities=len(group),
                win_rate=_rate(group, {"TP1_HIT", "TP2_HIT"}),
                tp1_rate=_rate(group, {"TP1_HIT", "TP2_HIT"}),
                tp2_rate=_rate(group, {"TP2_HIT"}),
                average_return=_average(returns),
            )
        )
    return tuple(
        sorted(results, key=lambda item: (-item.average_return, item.label))
    )
