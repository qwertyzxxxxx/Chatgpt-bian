from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.paper.models import PaperAccount, PaperOutcome, PaperSimulationSummary


INITIAL_EQUITY = Decimal("1000")
AGGRESSIVE_RISK_PCT = Decimal("5")
NORMAL_RISK_PCT = Decimal("3")
TARGETS = (Decimal("1500"), Decimal("2500"), Decimal("5000"), Decimal("10000"))


class PaperSimulator:
    """Deterministic research-only paper ledger; it has no exchange connectivity."""

    def __init__(self, repository: MarketDataRepository) -> None:
        self._repository = repository

    def simulate(self) -> PaperSimulationSummary:
        account = self._repository.load_or_create_paper_account(INITIAL_EQUITY, _utc_now())
        starting_equity = account.equity
        processed = skipped = 0
        for outcome in self._repository.load_pending_paper_outcomes():
            account, was_skipped = self._apply(account, outcome)
            processed += 1
            skipped += int(was_skipped)
        return PaperSimulationSummary(
            starting_equity=starting_equity,
            ending_equity=account.equity,
            processed_trades=processed,
            skipped_while_paused=skipped,
            mode=account.mode,
            consecutive_losses=account.consecutive_losses,
            paused_until=account.paused_until,
            current_target=account.current_target,
            aggressive_allowed=account.aggressive_allowed,
        )

    def _apply(self, account: PaperAccount, outcome: PaperOutcome) -> tuple[PaperAccount, bool]:
        signal_time = _parse(outcome.generated_at)
        paused_until = _parse(account.paused_until) if account.paused_until else None
        if account.mode == "PAUSED" and paused_until is not None and signal_time < paused_until:
            updated = PaperAccount(
                account.equity, "PAUSED", account.consecutive_losses,
                account.paused_until, account.current_target, outcome.generated_at,
            )
            self._repository.save_paper_trade(outcome, Decimal("0"), Decimal("0"), Decimal("0"),
                                              account.equity, "SKIPPED_PAUSED", updated)
            return updated, True

        mode = "NORMAL" if account.mode == "PAUSED" else account.mode
        risk_pct = AGGRESSIVE_RISK_PCT if mode == "AGGRESSIVE" else NORMAL_RISK_PCT
        risk_amount = _money(account.equity * risk_pct / Decimal("100"))
        realized_r = _realized_r(outcome)
        pnl = _money(risk_amount * realized_r)
        equity = max(Decimal("0"), _money(account.equity + pnl))

        if outcome.result == "LOSS":
            consecutive_losses = account.consecutive_losses + 1
            if consecutive_losses >= 3:
                mode = "PAUSED"
                pause_value = (signal_time + timedelta(hours=24)).isoformat(timespec="milliseconds")
            elif consecutive_losses >= 2:
                mode = "NORMAL"
                pause_value = None
            else:
                pause_value = None
        else:
            consecutive_losses = 0
            mode = "AGGRESSIVE"
            pause_value = None

        updated = PaperAccount(
            equity=equity,
            mode=mode,
            consecutive_losses=consecutive_losses,
            paused_until=pause_value,
            current_target=_target(equity),
            updated_at=outcome.generated_at,
        )
        self._repository.save_paper_trade(
            outcome, risk_pct, risk_amount, realized_r, equity, outcome.result, updated
        )
        return updated, False


def _realized_r(outcome: PaperOutcome) -> Decimal:
    if outcome.result == "LOSS":
        return Decimal("-1")
    if outcome.result == "EXPIRED":
        return Decimal("0")
    if outcome.direction == "LONG":
        risk = outcome.entry - outcome.stop_loss
        reward = (outcome.tp2 if outcome.result == "WIN_TP2" else outcome.tp1) - outcome.entry
    else:
        risk = outcome.stop_loss - outcome.entry
        reward = outcome.entry - (outcome.tp2 if outcome.result == "WIN_TP2" else outcome.tp1)
    return (reward / risk).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _target(equity: Decimal) -> str:
    for target in TARGETS:
        if equity < target:
            return str(target)
    return "COMPLETED"


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
