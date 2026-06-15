from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class HotlistCandidate:
    symbol: str
    direction: str
    change_24h_pct: Decimal
    quote_volume: Decimal


@dataclass(frozen=True, slots=True)
class HotlistEntryPlan:
    symbol: str
    direction: str
    current_price: Decimal
    change_24h_pct: Decimal
    quote_volume: Decimal
    volume_ratio_15m: Decimal
    ema20_15m: Decimal
    atr14: Decimal
    swing_high: Decimal
    swing_low: Decimal
    suggested_limit_entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    rr: Decimal
    expires_at: str
    reason: str


@dataclass(frozen=True, slots=True)
class HotlistWatchlistItem:
    symbol: str
    source: str
    first_seen_at: str
    last_seen_at: str
    expires_at: str
    observation_count: int
    last_rank: int
    status: str


@dataclass(frozen=True, slots=True)
class AIHotlistDecision:
    symbol: str
    approved: bool
    reason: str


@dataclass(frozen=True, slots=True)
class HotlistAIReview:
    symbol: str
    direction: str
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    rr: Decimal
    confidence: str
    reason: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class TrackedHotlistOpportunity:
    id: int | None
    symbol: str
    direction: str
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    rr: Decimal
    confidence: str
    created_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class HotlistOutcome:
    opportunity_id: int
    horizon_hours: int
    status: str
    evaluated_at: str
    return_pct: Decimal


@dataclass(frozen=True, slots=True)
class HotlistPerformanceSlice:
    label: str
    opportunities: int
    win_rate: Decimal
    tp1_rate: Decimal
    tp2_rate: Decimal
    average_return: Decimal


@dataclass(frozen=True, slots=True)
class HotlistPerformanceStatistics:
    total_opportunities: int
    win_rate: Decimal
    tp1_rate: Decimal
    tp2_rate: Decimal
    average_rr: Decimal
    average_return: Decimal
    confidence_performance: tuple[HotlistPerformanceSlice, ...]
    symbol_performance: tuple[HotlistPerformanceSlice, ...]


@dataclass(frozen=True, slots=True)
class HotlistAlert:
    symbol: str
    direction: str
    entry: Decimal
    created_at: str
    level: str
    plan: HotlistEntryPlan


@dataclass(frozen=True, slots=True)
class HotlistDailySummary:
    generated_at: str
    symbols_watched: int
    alerts_generated: int
    expired_symbols: int
    top_opportunities: tuple[HotlistEntryPlan, ...]
