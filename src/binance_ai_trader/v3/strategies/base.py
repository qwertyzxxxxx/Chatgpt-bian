"""V3 Strategy base class.

Rules for V3 strategies:
  - Only implement generate_candidates().
  - Return list[CandidateInput] — no Risk, no Dedup, no Settlement.
  - Never write to any table directly (except optional feature_store via features()).
  - Never send Telegram messages.

The pipeline handles: Risk → Dedup → Candidate save → Push Queue → Paper → Settlement.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from binance_ai_trader.v3.candidates.repository import CandidateInput


class V3Strategy(ABC):
    """Abstract base for all V3 strategies."""

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Unique strategy identifier (e.g. 'hotlist_momentum_v3')."""

    @abstractmethod
    def generate_candidates(self, now: datetime | None = None) -> list[CandidateInput]:
        """Scan the market and return raw candidates.

        - Must be idempotent (safe to call multiple times).
        - Must NOT perform Risk or Dedup filtering (pipeline does this).
        - Must NOT write to any shared DB table.
        - Should return an empty list on non-fatal errors (and log them).
        - now is injected for testability; use datetime.now(UTC) as default.
        """

    def features(self, inp: CandidateInput) -> dict:
        """Optional: return raw factor dict to persist in v3_feature_store.

        Default implementation extracts the standard Candidate fields.
        Override to add strategy-specific factors (ATR, EMAs, volume ratios…).
        """
        return {
            "strategy_id": inp.strategy_id,
            "symbol": inp.symbol,
            "direction": inp.direction,
            "entry": inp.entry,
            "sl": inp.sl,
            "tp1": inp.tp1,
            "tp2": inp.tp2,
            "rr": inp.rr,
            "confidence": inp.confidence,
            "stop_pct": inp.stop_pct,
            "change_24h": inp.change_24h,
            "quote_volume": inp.quote_volume,
            "volume_ratio": inp.volume_ratio,
            "atr": inp.atr,
            "ema20": inp.ema20,
            "ema60": inp.ema60,
            "market_regime": inp.market_regime,
        }

    def _now(self, now: datetime | None) -> datetime:
        return (now or datetime.now(UTC)).astimezone(UTC)
