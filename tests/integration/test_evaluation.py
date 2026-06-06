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


def trade_signal(symbol: str, direction: str = "LONG") -> TradeSignal:
    short = direction == "SHORT"
    return TradeSignal(
        symbol=symbol,
        direction=direction,
        score=90,
        entry=Decimal("100"),
        latest_close=Decimal("100"),
        stop_loss=Decimal("105" if short else "95"),
        stop_loss_pct=Decimal("5"),
        tp1=Decimal("95" if short else "105"),
        tp2=Decimal("90" if short else "110"),
        rr_tp1=Decimal("1"),
        rr_tp2=Decimal("2"),
        logic_summary="fixture",
    )


def bars_for(symbol: str, result: str, direction: str = "LONG"):
    values = []
    for index in range(96):
        low, high = "99", "101"
        if direction == "LONG":
            if result == "LOSS" and index == 1:
                low = "94"
            elif result == "TP1_HIT" and index == 2:
                high = "106"
            elif result == "WIN_TP2" and index == 3:
                high = "111"
        else:
            if result == "LOSS" and index == 1:
                high = "106"
            elif result == "TP1_HIT" and index == 2:
                low = "94"
            elif result == "WIN_TP2" and index == 3:
                low = "89"
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
            for direction_index, direction in enumerate(("LONG", "SHORT")):
                for index, outcome in enumerate(outcomes, start=1):
                    sequence = direction_index * 4 + index
                    run_id = f"run-{sequence}"
                    symbol = f"COIN{sequence}USDT"
                    repository.start_run(run_id, f"1970-01-01T00:00:{sequence:02d}.000+00:00")
                    repository.save_signals(run_id, (trade_signal(symbol, direction),), GENERATED_AT)
                    repository.save_klines(bars_for(symbol, outcome, direction))
        finally:
            repository.close()

    def test_evaluates_signals_persists_results_and_returns_summary(self) -> None:
        self.seed()
        repository = MarketDataRepository(self.database)
        try:
            summary = SignalEvaluator(repository).evaluate_all()
        finally:
            repository.close()

        self.assertEqual(8, summary.total_signals)
        self.assertEqual(2, summary.win_tp2_count)
        self.assertEqual(2, summary.tp1_hit_count)
        self.assertEqual(2, summary.loss_count)
        self.assertEqual(2, summary.expired_count)
        self.assertEqual(50.0, summary.tp1_hit_rate)
        self.assertEqual(25.0, summary.tp2_win_rate)
        self.assertEqual(25.0, summary.loss_rate)
        self.assertEqual(25.0, summary.expired_rate)
        self.assertEqual(0.5, summary.expectancy_r)
        self.assertEqual(4, summary.by_direction["LONG"].total_signals)
        self.assertEqual(4, summary.by_direction["SHORT"].total_signals)
        self.assertEqual(0.5, summary.by_direction["SHORT"].expectancy_r)
        self.assertEqual(4.75, summary.average_max_favorable_pct)
        self.assertEqual(2.25, summary.average_max_adverse_pct)

        with closing(sqlite3.connect(self.database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(signal_evaluations)")}
            rows = connection.execute(
                """
                SELECT signal_run_id, symbol, direction, entry, stop_loss, tp1, tp2, result,
                       max_favorable_pct, max_adverse_pct, bars_to_result, evaluated_at
                FROM signal_evaluations ORDER BY signal_run_id
                """
            ).fetchall()
        self.assertEqual(8, len(rows))
        self.assertEqual({"LONG", "SHORT"}, {row[2] for row in rows})
        self.assertEqual({"WIN_TP2", "TP1_HIT", "LOSS", "EXPIRED"}, {row[7] for row in rows})
        self.assertTrue(
            {
                "signal_run_id", "symbol", "direction", "entry", "stop_loss", "tp1", "tp2", "result",
                "max_favorable_pct", "max_adverse_pct", "bars_to_result", "evaluated_at",
            }.issubset(columns)
        )
        self.assertTrue(all(datetime.fromisoformat(row[11]).tzinfo == UTC for row in rows))

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
                "tp1_hit_rate", "tp2_win_rate", "loss_rate", "expired_rate", "expectancy_r",
                "average_max_favorable_pct", "average_max_adverse_pct", "by_direction",
            },
            set(payload),
        )
        self.assertEqual(8, payload["total_signals"])
        self.assertEqual({"LONG", "SHORT"}, set(payload["by_direction"]))


if __name__ == "__main__":
    unittest.main()
