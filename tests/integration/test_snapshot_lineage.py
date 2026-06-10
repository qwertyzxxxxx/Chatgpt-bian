from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.domain.models import BacktestResult, SignalEvaluation, TradeSignal
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


CREATED_AT = "2026-06-07T00:00:00.000+00:00"


def signal(symbol: str) -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        direction="LONG",
        score=90.0,
        entry=Decimal("100"),
        latest_close=Decimal("100"),
        stop_loss=Decimal("95"),
        stop_loss_pct=Decimal("5"),
        tp1=Decimal("105"),
        tp2=Decimal("110"),
        rr_tp1=Decimal("1"),
        rr_tp2=Decimal("2"),
        logic_summary="snapshot lineage fixture",
    )


def evaluation(run_id: str, symbol: str, snapshot_id: str | None) -> SignalEvaluation:
    return SignalEvaluation(
        signal_run_id=run_id,
        symbol=symbol,
        direction="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        tp1=Decimal("105"),
        tp2=Decimal("110"),
        result="WIN_TP2",
        max_favorable_pct=Decimal("10"),
        max_adverse_pct=Decimal("1"),
        bars_to_result=4,
        snapshot_id=snapshot_id,
    )


class SnapshotLineageIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "lineage.db"
        self.repository = MarketDataRepository(self.database)

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_each_scan_run_creates_exactly_one_snapshot(self) -> None:
        self.repository.start_run("scan-1", CREATED_AT)
        snapshot = self.repository.load_snapshot_for_run("scan-1")

        with closing(sqlite3.connect(self.database)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM analysis_snapshots WHERE collection_run_id='scan-1'"
            ).fetchone()[0]

        self.assertEqual(1, count)
        self.assertEqual("snapshot-scan-1", snapshot.snapshot_id)
        self.assertEqual("SCAN", snapshot.snapshot_type)
        self.assertEqual("scan-1", snapshot.source_ref)

    def test_signals_reference_their_scan_snapshot(self) -> None:
        self.repository.start_run("scan-1", CREATED_AT)
        self.repository.save_signals("scan-1", (signal("BTCUSDT"),), CREATED_AT)

        with closing(sqlite3.connect(self.database)) as connection:
            stored = connection.execute(
                "SELECT run_id, symbol, snapshot_id FROM signals"
            ).fetchone()

        self.assertEqual(("scan-1", "BTCUSDT", "snapshot-scan-1"), stored)

    def test_evaluations_cannot_mix_snapshots_across_runs(self) -> None:
        self.repository.start_run("scan-1", CREATED_AT)
        self.repository.start_run("scan-2", "2026-06-07T00:15:00.000+00:00")
        self.repository.save_signals("scan-1", (signal("BTCUSDT"),), CREATED_AT)
        self.repository.save_signals(
            "scan-2", (signal("ETHUSDT"),), "2026-06-07T00:15:00.000+00:00"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.save_signal_evaluations(
                (evaluation("scan-1", "BTCUSDT", "snapshot-scan-2"),), CREATED_AT
            )

        self.repository.save_signal_evaluations(
            (evaluation("scan-1", "BTCUSDT", None),), CREATED_AT
        )
        with closing(sqlite3.connect(self.database)) as connection:
            stored = connection.execute(
                "SELECT signal_run_id, symbol, snapshot_id FROM signal_evaluations"
            ).fetchone()
        self.assertEqual(("scan-1", "BTCUSDT", "snapshot-scan-1"), stored)

    def test_backtest_results_reference_point_in_time_snapshot(self) -> None:
        self.repository.start_backtest_run("bt-1", CREATED_AT, None, None, 1)
        item = BacktestResult(
            evaluation_time_ms=1_000,
            symbol="BTCUSDT",
            direction="LONG",
            combined_regime="BULL",
            sector="LAYER1",
            sector_rank=1,
            score=90.0,
            entry=Decimal("100"),
            stop_loss=Decimal("95"),
            tp1=Decimal("105"),
            tp2=Decimal("110"),
            rr_tp1=Decimal("1"),
            rr_tp2=Decimal("2"),
            result="WIN_TP2",
            bars_to_result=4,
            realized_r=Decimal("2"),
        )
        self.repository.save_backtest_results("bt-1", (item,))

        with closing(sqlite3.connect(self.database)) as connection:
            stored = connection.execute(
                """SELECT r.snapshot_id, s.snapshot_type, s.source_ref, s.data_cutoff_ms
                   FROM backtest_results AS r
                   JOIN analysis_snapshots AS s ON s.snapshot_id=r.snapshot_id"""
            ).fetchone()

        self.assertEqual(
            ("snapshot-backtest-bt-1-1000", "BACKTEST", "bt-1:1000", 1_000), stored
        )


if __name__ == "__main__":
    unittest.main()
