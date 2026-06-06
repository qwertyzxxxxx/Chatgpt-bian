from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PaperOutcome:
    signal_run_id: str
    symbol: str
    direction: str
    result: str
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    generated_at: str


@dataclass(frozen=True, slots=True)
class PaperAccount:
    equity: Decimal
    mode: str
    consecutive_losses: int
    paused_until: str | None
    current_target: str
    updated_at: str

    @property
    def aggressive_allowed(self) -> bool:
        return self.mode == "AGGRESSIVE" and self.paused_until is None


@dataclass(frozen=True, slots=True)
class PaperSimulationSummary:
    starting_equity: Decimal
    ending_equity: Decimal
    processed_trades: int
    skipped_while_paused: int
    mode: str
    consecutive_losses: int
    paused_until: str | None
    current_target: str
    aggressive_allowed: bool
