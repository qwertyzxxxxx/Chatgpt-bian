from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WatchItem:
    watch_id: str
    symbol: str
    first_seen_at: str
    last_seen_at: str
    first_rank_type: str
    latest_rank_type: str
    best_rank_position: int
    latest_rank_position: int
    first_change_24h: str
    latest_change_24h: str
    quote_volume: str
    appearances_24h: int
    status: str


@dataclass
class WatchDecision:
    decision: str
    best_symbol: str
    direction: str
    rating: str
    entry: str
    stop_loss: str
    tp1: str
    tp2: str
    rr: str
    risk_level: str
    should_trade: bool
    reasons: list[str]
    reject_reasons: list[dict[str, str]]
    data_quality: str
    raw_response: str = ""

    @classmethod
    def no_trade(cls, raw_response: str = "") -> "WatchDecision":
        return cls(
            decision="NO_TRADE",
            best_symbol="NONE",
            direction="UNKNOWN",
            rating="C",
            entry="UNKNOWN",
            stop_loss="UNKNOWN",
            tp1="UNKNOWN",
            tp2="UNKNOWN",
            rr="UNKNOWN",
            risk_level="HIGH",
            should_trade=False,
            reasons=["暂无合适机会"],
            reject_reasons=[],
            data_quality="PARTIAL",
            raw_response=raw_response,
        )


@dataclass
class SkipResult:
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"status": "SKIPPED", "reason": self.reason}


@dataclass
class PoolStatus:
    new_count: int
    active_count: int
    open_count: int
    closed_count: int
    expired_count: int
    top_active: list[WatchItem]


@dataclass
class PoolSummary:
    total_reviews: int
    trade_count: int
    no_trade_count: int
    open_count: int
    tp1_count: int
    tp2_count: int
    sl_count: int
    timeout_count: int
    win_rate: str


@dataclass
class WatchCandidateForGemini:
    symbol: str
    latest_rank_type: str
    latest_rank_position: int
    best_rank_position: int
    latest_change_24h: str
    first_change_24h: str
    quote_volume: str
    active_duration_minutes: int
    appearances_24h: int
    gainer_candidate: bool
    loser_candidate: bool
    volume_candidate: bool
    data_quality: str = "FULL"
    m15: dict[str, Any] = field(default_factory=dict)
    h1: dict[str, Any] = field(default_factory=dict)
    h4: dict[str, Any] = field(default_factory=dict)
    d1: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "latest_rank_type": self.latest_rank_type,
            "latest_rank_position": self.latest_rank_position,
            "best_rank_position": self.best_rank_position,
            "latest_change_24h": self.latest_change_24h,
            "first_change_24h": self.first_change_24h,
            "quote_volume": self.quote_volume,
            "active_duration_minutes": self.active_duration_minutes,
            "appearances_24h": self.appearances_24h,
            "gainer_candidate": self.gainer_candidate,
            "loser_candidate": self.loser_candidate,
            "volume_candidate": self.volume_candidate,
            "data_quality": self.data_quality,
            "m15": self.m15,
            "h1": self.h1,
            "h4": self.h4,
            "d1": self.d1,
        }
