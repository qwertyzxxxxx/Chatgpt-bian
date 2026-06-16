from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


RESULT_OPEN = "OPEN"
RESULT_TP1 = "TP1"
RESULT_TP2 = "TP2"
RESULT_SL = "SL"
RESULT_TIMEOUT = "TIMEOUT"

STRATEGY_HOTLIST = "hotlist"
STRATEGY_AI_MACRO = "ai_macro"
STRATEGY_GEMINI = "gemini_committee"

ALL_RESULTS = (RESULT_TP1, RESULT_TP2, RESULT_SL, RESULT_TIMEOUT, RESULT_OPEN)
WIN_RESULTS = (RESULT_TP1, RESULT_TP2)
LOSS_RESULTS = (RESULT_SL, RESULT_TIMEOUT)


@dataclass
class StrategyResult:
    result_id: str
    strategy: str
    symbol: str
    direction: str
    entry: str
    stop_loss: str
    tp1: str
    tp2: str
    opened_at: str
    source_id: str
    closed_at: Optional[str] = None
    result: str = RESULT_OPEN
    pnl_pct: Optional[float] = None
    rr_realized: Optional[float] = None
    duration_minutes: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "result_id": self.result_id,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "result": self.result,
            "pnl_pct": self.pnl_pct,
            "rr_realized": self.rr_realized,
            "duration_minutes": self.duration_minutes,
            "source_id": self.source_id,
        }

    @classmethod
    def from_row(cls, row: tuple, columns: list) -> "StrategyResult":
        d = dict(zip(columns, row))
        return cls(
            result_id=d["result_id"],
            strategy=d["strategy"],
            symbol=d["symbol"],
            direction=d["direction"],
            entry=d["entry"],
            stop_loss=d["stop_loss"],
            tp1=d["tp1"],
            tp2=d["tp2"],
            opened_at=d["opened_at"],
            source_id=d["source_id"],
            closed_at=d.get("closed_at"),
            result=d.get("result", RESULT_OPEN),
            pnl_pct=float(d["pnl_pct"]) if d.get("pnl_pct") is not None else None,
            rr_realized=float(d["rr_realized"]) if d.get("rr_realized") is not None else None,
            duration_minutes=int(d["duration_minutes"]) if d.get("duration_minutes") is not None else None,
        )


@dataclass
class StrategyStats:
    strategy: str
    total: int = 0
    tp1: int = 0
    tp2: int = 0
    sl: int = 0
    timeout: int = 0
    open_count: int = 0
    win_rate: float = 0.0
    avg_rr: float = 0.0
    avg_pnl_pct: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0


@dataclass
class Leaderboard:
    entries: List[StrategyStats] = field(default_factory=list)
