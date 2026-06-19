from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TimeframeIndicators:
    trend: str = "UNKNOWN"
    ema10: str = "UNKNOWN"
    ema20: str = "UNKNOWN"
    ema60: str = "UNKNOWN"
    rsi14: str = "UNKNOWN"
    atr_pct: str = "UNKNOWN"
    volume_ratio_20: str = "UNKNOWN"
    recent_swing_high: str = "UNKNOWN"
    recent_swing_low: str = "UNKNOWN"
    change_30d: str = "UNKNOWN"
    recent_high_30d: str = "UNKNOWN"
    recent_low_30d: str = "UNKNOWN"

    def to_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Candidate:
    symbol: str
    source: str
    direction: str
    entry: str
    stop_loss: str
    tp1: str
    tp2: str
    rr: str
    stop_pct: str = "UNKNOWN"
    current_price: str = "UNKNOWN"
    change_24h: str = "UNKNOWN"
    quote_volume: str = "UNKNOWN"
    hotlist_rank: str = "UNKNOWN"
    first_seen_at: str = "UNKNOWN"
    active_duration_minutes: str = "UNKNOWN"
    appearance_count_24h: str = "UNKNOWN"
    appearance_count_7d: str = "UNKNOWN"
    data_quality: str = "FULL"
    m15: TimeframeIndicators = field(default_factory=TimeframeIndicators)
    h1: TimeframeIndicators = field(default_factory=TimeframeIndicators)
    h4: TimeframeIndicators = field(default_factory=TimeframeIndicators)
    d1: TimeframeIndicators = field(default_factory=TimeframeIndicators)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "direction": self.direction,
            "current_price": self.current_price,
            "change_24h": self.change_24h,
            "quote_volume": self.quote_volume,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "rr": self.rr,
            "stop_pct": self.stop_pct,
            "data_quality": self.data_quality,
            "m15": self.m15.to_dict(),
            "h1": self.h1.to_dict(),
            "h4": self.h4.to_dict(),
            "d1": self.d1.to_dict(),
            "hotlist_rank": self.hotlist_rank,
            "first_seen_at": self.first_seen_at,
            "active_duration_minutes": self.active_duration_minutes,
            "appearance_count_24h": self.appearance_count_24h,
            "appearance_count_7d": self.appearance_count_7d,
        }


@dataclass
class CommitteeDecision:
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
    def no_trade(cls, raw_response: str = "") -> "CommitteeDecision":
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
            reasons=["No suitable opportunity identified"],
            reject_reasons=[],
            data_quality="PARTIAL",
            raw_response=raw_response,
        )


@dataclass
class SkipResult:
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"status": "SKIPPED", "reason": self.reason}
