from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MacroAnalysis:
    generated_at: str
    btc_change_pct: Decimal
    eth_change_pct: Decimal
    market_state: str
    risk_grade: str
    trade_bias: str


@dataclass(frozen=True, slots=True)
class AIMacroScore:
    symbol: str
    direction: str
    score: int
    trend_score: int
    momentum_score: int
    volume_score: int
    structure_score: int
    risk_score: int
    reason: str
    entry: Decimal | None
    stop_loss: Decimal | None
    tp1: Decimal | None
    tp2: Decimal | None


@dataclass(frozen=True, slots=True)
class AIMacroTrade:
    trade_id: str
    created_at: str
    symbol: str
    direction: str
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    score: int
    market_state: str
    risk_grade: str
    reason: str
    status: str
    pnl_pct: Decimal | None
    closed_at: str | None


@dataclass(frozen=True, slots=True)
class AIMacroPerformance:
    total_trades: int
    open_trades: int
    closed_trades: int
    win_count: int
    tp1_count: int
    tp2_count: int
    stop_count: int
    expired_count: int
    win_rate: Decimal
    tp1_rate: Decimal
    tp2_rate: Decimal
    avg_pnl_pct: Decimal
    virtual_balance: Decimal
