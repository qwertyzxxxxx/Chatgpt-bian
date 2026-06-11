from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.analyze_capital_flow import CapitalFlowAnalyzer
from binance_ai_trader.capital import (
    CapitalFlowHistory,
    CapitalObservation,
    CapitalSnapshot,
)
from binance_ai_trader.domain.models import SymbolScore
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from tests.integration.test_signals import member


T = 1_800_000_000_000
HOUR = 3_600_000
CREATED_AT = "2027-01-15T08:00:00.000+00:00"


def observations(snapshot_id: str, *, future: bool = False):
    values = (
        (("OPEN_INTEREST", T + HOUR, "9999"),
         ("FUNDING_RATE", T + HOUR, "0.001"),
         ("LONG_SHORT_RATIO", T + HOUR, "3"),
         ("QUOTE_VOLUME_24H", T + HOUR, "9999"))
        if future else
        (("OPEN_INTEREST", T - 24 * HOUR, "100"),
         ("OPEN_INTEREST", T - 4 * HOUR, "112"),
         ("OPEN_INTEREST", T - HOUR, "118"),
         ("OPEN_INTEREST", T, "120"),
         ("FUNDING_RATE", T - 8 * HOUR, "0.0001"),
         ("LONG_SHORT_RATIO", T - HOUR, "1.05"),
         ("QUOTE_VOLUME_24H", T, "150"))
    )
    return tuple(
        CapitalObservation("BTCUSDT", metric, timestamp, Decimal(value), snapshot_id)
        for metric, timestamp, value in values
    )


class HistoricalCapitalClient:
    def open_interest_history(self, symbol, limit, start_time_ms, end_time_ms):
        return tuple(
            (timestamp, value) for metric, timestamp, value in (
                ("OPEN_INTEREST", T - 24 * HOUR, Decimal("100")),
                ("OPEN_INTEREST", T - 4 * HOUR, Decimal("112")),
                ("OPEN_INTEREST", T - HOUR, Decimal("118")),
                ("OPEN_INTEREST", T, Decimal("120")),
            ) if start_time_ms <= timestamp <= end_time_ms
        )

    def funding_rate_history(self, symbol, limit, start_time_ms, end_time_ms):
        return ((T - 8 * HOUR, Decimal("0.0001")),)

    def global_long_short_ratio_history(self, symbol, limit, start_time_ms, end_time_ms):
        return ((T - HOUR, Decimal("1.05")),)


class CapitalHistoryIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "capital.db"
        self.repository = MarketDataRepository(self.database)
        self.repository.start_run("scan-1", CREATED_AT)
        self.snapshot = self.repository.load_snapshot_for_run("scan-1")

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_historical_capital_flow_is_reconstructed_from_raw_observations(self) -> None:
        self.repository.save_capital_observations(
            observations(self.snapshot.snapshot_id), CREATED_AT
        )

        result = CapitalFlowHistory(self.repository).score_at("point-T", "BTCUSDT", T)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(Decimal("20.00"), result.oi_change_24h_pct)
        self.assertGreater(result.capital_score, Decimal("70"))

    def test_point_in_time_read_cannot_access_future_capital_data(self) -> None:
        self.repository.save_capital_observations(
            observations(self.snapshot.snapshot_id), CREATED_AT
        )
        before = CapitalFlowHistory(self.repository).score_at("point-T", "BTCUSDT", T)
        self.repository.save_capital_observations(
            observations(self.snapshot.snapshot_id, future=True), CREATED_AT
        )
        after = CapitalFlowHistory(self.repository).score_at("point-T", "BTCUSDT", T)

        self.assertEqual(before, after)

    def test_raw_capital_observations_are_immutable(self) -> None:
        self.repository.save_capital_observations(
            observations(self.snapshot.snapshot_id), CREATED_AT
        )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repository._connection:
                self.repository._connection.execute(
                    """UPDATE capital_flow_observations SET value='999'
                       WHERE symbol='BTCUSDT' AND metric='OPEN_INTEREST'"""
                )

    def test_live_and_backtest_paths_are_equivalent_at_same_cutoff(self) -> None:
        self.repository.save_capital_observations(
            observations(self.snapshot.snapshot_id), CREATED_AT
        )
        history = CapitalFlowHistory(self.repository)

        live = history.score_at("scan-1", "BTCUSDT", T)
        backtest = history.score_at("backtest-point", "BTCUSDT", T)

        self.assertIsNotNone(live)
        self.assertIsNotNone(backtest)
        assert live is not None and backtest is not None
        self.assertEqual(live.capital_score, backtest.capital_score)
        self.assertEqual(live.oi_change_1h_pct, backtest.oi_change_1h_pct)
        self.assertEqual(live.funding_score, backtest.funding_score)
        self.assertEqual(live.crowding_score, backtest.crowding_score)

    def test_live_analyzer_and_point_in_time_replay_use_equivalent_calculation(self) -> None:
        self.repository.save_universe(
            "scan-1", (member("BTCUSDT"),), CREATED_AT
        )
        self.repository.save_scores(
            "scan-1", (SymbolScore("BTCUSDT", 90, {}, "v1"),), CREATED_AT
        )

        live = CapitalFlowAnalyzer(
            self.repository, HistoricalCapitalClient()
        ).analyze_latest(snapshot_id=self.snapshot.snapshot_id)[0]
        replay = CapitalFlowHistory(self.repository).score_at(
            "backtest-point", "BTCUSDT", T
        )

        self.assertIsNotNone(replay)
        assert replay is not None
        self.assertEqual(live.capital_score, replay.capital_score)
        self.assertEqual(live.oi_change_24h_pct, replay.oi_change_24h_pct)
        self.assertEqual(live.funding_score, replay.funding_score)
        self.assertEqual(live.crowding_score, replay.crowding_score)

    def test_raw_and_derived_capital_records_retain_snapshot_lineage(self) -> None:
        self.repository.save_capital_observations(
            observations(self.snapshot.snapshot_id), CREATED_AT
        )
        derived = CapitalFlowHistory(self.repository).score_at("scan-1", "BTCUSDT", T)
        assert derived is not None
        self.repository.save_capital_snapshots((derived,), CREATED_AT)

        with closing(sqlite3.connect(self.database)) as connection:
            raw_ids = {
                row[0] for row in connection.execute(
                    "SELECT DISTINCT ingested_snapshot_id FROM capital_flow_observations"
                )
            }
            derived_id = connection.execute(
                "SELECT snapshot_id FROM capital_snapshots WHERE run_id='scan-1'"
            ).fetchone()[0]

        self.assertEqual({self.snapshot.snapshot_id}, raw_ids)
        self.assertEqual(self.snapshot.snapshot_id, derived_id)


class CapitalHistoryMigrationTest(unittest.TestCase):
    def test_existing_capital_snapshots_are_backfilled_to_scan_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """CREATE TABLE collection_runs (
                           id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                           status TEXT NOT NULL, universe_size INTEGER NOT NULL DEFAULT 0,
                           kline_count INTEGER NOT NULL DEFAULT 0, error_summary TEXT
                       )"""
                )
                connection.execute(
                    "INSERT INTO collection_runs (id, started_at, status) VALUES (?, ?, 'SUCCEEDED')",
                    ("legacy", CREATED_AT),
                )
                connection.execute(
                    """CREATE TABLE capital_snapshots (
                           run_id TEXT NOT NULL, symbol TEXT NOT NULL, oi_current TEXT NOT NULL,
                           oi_change_1h_pct TEXT NOT NULL, oi_change_4h_pct TEXT NOT NULL,
                           oi_change_24h_pct TEXT NOT NULL, current_funding_rate TEXT NOT NULL,
                           funding_score TEXT NOT NULL, long_short_ratio TEXT NOT NULL,
                           crowding_score TEXT NOT NULL, volume_expansion_score TEXT NOT NULL,
                           oi_expansion_score TEXT NOT NULL, capital_score TEXT NOT NULL,
                           calculated_at TEXT NOT NULL, PRIMARY KEY(run_id, symbol)
                       )"""
                )
                connection.execute(
                    """INSERT INTO capital_snapshots VALUES (
                           'legacy','BTCUSDT','100','1','2','3','0.0001','100',
                           '1','100','50','50','65',?
                       )""",
                    (CREATED_AT,),
                )
                connection.commit()

            repository = MarketDataRepository(database)
            try:
                columns = {
                    row[1] for row in repository._connection.execute(
                        "PRAGMA table_info(capital_snapshots)"
                    )
                }
                linked = repository._connection.execute(
                    "SELECT snapshot_id FROM capital_snapshots WHERE run_id='legacy'"
                ).fetchone()[0]
                raw_table = repository._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='capital_flow_observations'"
                ).fetchone()
            finally:
                repository.close()

        self.assertIn("snapshot_id", columns)
        self.assertEqual("snapshot-legacy", linked)
        self.assertEqual(("capital_flow_observations",), raw_table)


if __name__ == "__main__":
    unittest.main()
