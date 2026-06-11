from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from binance_ai_trader.domain.models import SignalEvaluation, TradeSignal
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.paper.service import PaperSimulator


def signal(symbol: str) -> TradeSignal:
    return TradeSignal(
        symbol=symbol, direction="LONG", score=90, entry=Decimal("100"),
        latest_close=Decimal("100"), stop_loss=Decimal("95"), stop_loss_pct=Decimal("5"),
        tp1=Decimal("105"), tp2=Decimal("110"), rr_tp1=Decimal("1"), rr_tp2=Decimal("2"),
        logic_summary="paper fixture",
    )


def evaluation(run_id: str, symbol: str, result: str) -> SignalEvaluation:
    return SignalEvaluation(
        signal_run_id=run_id, symbol=symbol, direction="LONG", entry=Decimal("100"),
        stop_loss=Decimal("95"), tp1=Decimal("105"), tp2=Decimal("110"), result=result,
        max_favorable_pct=Decimal("0"), max_adverse_pct=Decimal("0"), bars_to_result=1,
    )


class PaperSimulatorTest(unittest.TestCase):
    def test_risk_downgrade_pause_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = MarketDataRepository(Path(directory) / "paper.db")
            try:
                for index in range(1, 5):
                    run_id, symbol = f"run-{index}", f"COIN{index}USDT"
                    generated = f"2026-06-06T0{index}:00:00.000+00:00"
                    repository.start_run(run_id, generated)
                    repository.save_signals(run_id, (signal(symbol),), generated)
                    repository.save_signal_evaluations(
                        (evaluation(run_id, symbol, "LOSS"),), generated
                    )
                summary = PaperSimulator(repository).simulate()
                repeated = PaperSimulator(repository).simulate()
                rows = repository._connection.execute(
                    "SELECT risk_pct, action FROM paper_trades ORDER BY processed_at"
                ).fetchall()
            finally:
                repository.close()

        self.assertEqual(Decimal("1000"), summary.starting_equity)
        self.assertEqual(Decimal("875.42"), summary.ending_equity)
        self.assertEqual("PAUSED", summary.mode)
        self.assertEqual(3, summary.consecutive_losses)
        self.assertEqual(1, summary.skipped_while_paused)
        self.assertFalse(summary.aggressive_allowed)
        self.assertEqual([("5", "LOSS"), ("5", "LOSS"), ("3", "LOSS"), ("0", "SKIPPED_PAUSED")], rows)
        self.assertEqual(0, repeated.processed_trades)
        self.assertEqual(summary.ending_equity, repeated.ending_equity)

    def test_winner_advances_equity_toward_first_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = MarketDataRepository(Path(directory) / "paper.db")
            try:
                repository.start_run("win", "2026-06-06T01:00:00.000+00:00")
                repository.save_signals("win", (signal("WINUSDT"),), "2026-06-06T01:00:00.000+00:00")
                repository.save_signal_evaluations(
                    (evaluation("win", "WINUSDT", "WIN_TP2"),), "2026-06-06T02:00:00.000+00:00"
                )
                summary = PaperSimulator(repository).simulate()
            finally:
                repository.close()
        self.assertEqual(Decimal("1100.00"), summary.ending_equity)
        self.assertEqual("1500", summary.current_target)
        self.assertTrue(summary.aggressive_allowed)


if __name__ == "__main__":
    unittest.main()
