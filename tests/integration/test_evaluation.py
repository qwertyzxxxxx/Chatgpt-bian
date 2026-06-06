from contextlib import closing, redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.evaluate_signals import SignalEvaluator
from binance_ai_trader.domain.models import TradeSignal
from binance_ai_trader.entrypoints.cli import main
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from tests.unit.test_evaluation_engine import bar


GENERATED_AT = "1970-01-01T00:00:01.000+00:00"


def trade_signal(symbol: str) -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        direction="LONG",
        score=90,
        entry=Decimal("100"),
        latest_close=Decimal("100"),
        stop_loss=Decimal("95"),
        stop_loss_pct=Decimal("5"),
        tp1=Decimal("105"),
        tp2=Decimal("110"),
        rr_tp1=Decimal("1"),
        rr_tp2=Decimal("2"),
        logic_summary="fixture",
    )


def bars_for(symbol: str, result: str):
    values = []
    for index in range(96):
        low, high = "99", "101"
        if result == "LOSS" and index == 1:
            low = "94"
        elif result == "TP1_HIT" and index == 2:
            high = "106"
        elif result == "WIN_TP2" and index == 3:
            high = "111"
        values.append(bar(index, low=low, high=high, symbol=symbol))
    return tuple(values)


class SignalEvaluationIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "market.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def seed(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            outcomes = ("WIN_TP2", "TP1_HIT", "LOSS", "EXPIRED")
            for index, outcome in enumerate(outcomes, start=1):
                run_id = f"run-{index}"
                symbol = f"COIN{index}USDT"
                repository.start_run(run_id, f"1970-01-01T00:00:0{index}.000+00:00")
                repository.save_signals(run_id, (trade_signal(symbol),), GENERATED_AT)
                repository.save_klines(bars_for(symbol, outcome))
        finally:
            repository.close()

    def test_evaluates_signals_persists_results_and_returns_summary(self) -> None:
        self.seed()
        repository = MarketDataRepository(self.database)
        try:
            summary = SignalEvaluator(repository).evaluate_all()
        finally:
            repository.close()

        self.assertEqual(4, summary.total_signals)
        self.assertEqual(1, summary.win_tp2_count)
        self.assertEqual(1, summary.tp1_hit_count)
        self.assertEqual(1, summary.loss_count)
        self.assertEqual(1, summary.expired_count)
        self.assertEqual(50.0, summary.tp1_hit_rate)
        self.assertEqual(25.0, summary.tp2_win_rate)
        self.assertEqual(25.0, summary.loss_rate)
        self.assertEqual(4.75, summary.average_max_favorable_pct)
        self.assertEqual(2.25, summary.average_max_adverse_pct)

        with closing(sqlite3.connect(self.database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(signal_evaluations)")}
            rows = connection.execute(
                """
                SELECT signal_run_id, symbol, entry, stop_loss, tp1, tp2, result,
                       max_favorable_pct, max_adverse_pct, bars_to_result, evaluated_at
                FROM signal_evaluations ORDER BY signal_run_id
                """
            ).fetchall()
        self.assertEqual(4, len(rows))
        self.assertEqual({"WIN_TP2", "TP1_HIT", "LOSS", "EXPIRED"}, {row[6] for row in rows})
        self.assertTrue(
            {
                "signal_run_id", "symbol", "entry", "stop_loss", "tp1", "tp2", "result",
                "max_favorable_pct", "max_adverse_pct", "bars_to_result", "evaluated_at",
            }.issubset(columns)
        )
        self.assertTrue(all(datetime.fromisoformat(row[10]).tzinfo == UTC for row in rows))

    def test_evaluate_cli_prints_required_summary_json(self) -> None:
        self.seed()
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["evaluate", "--database", str(self.database)])
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(
            {
                "total_signals", "win_tp2_count", "tp1_hit_count", "loss_count", "expired_count",
                "tp1_hit_rate", "tp2_win_rate", "loss_rate", "average_max_favorable_pct",
                "average_max_adverse_pct",
            },
            set(payload),
        )
        self.assertEqual(4, payload["total_signals"])


if __name__ == "__main__":
    unittest.main()
