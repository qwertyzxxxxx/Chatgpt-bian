from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.generate_signals import SignalGenerator
from binance_ai_trader.application.score_market_data import MarketScorer
from binance_ai_trader.capital import CapitalFlowHistory, CapitalObservation, CapitalSnapshot
from binance_ai_trader.domain.models import BacktestResult, MarketRegime, SymbolScore
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.space import SpaceSnapshot
from tests.integration.test_signals import member
from tests.unit.test_scoring_engine import market
from tests.unit.test_signal_engine import signal_klines


STARTED_AT = "2026-06-08T00:00:00.000+00:00"
T = int(datetime.fromisoformat(STARTED_AT).astimezone(UTC).timestamp() * 1000)
HOUR = 3_600_000


class DataQualityStatusIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "quality.db"
        self.repository = MarketDataRepository(self.database)

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_partial_scan_is_visible_on_scan_and_scores(self) -> None:
        self.repository.start_run("run-1", STARTED_AT)
        self.repository.save_universe("run-1", (member("BTCUSDT"),), STARTED_AT)
        for bars in market("BTCUSDT", 0.005).values():
            self.repository.save_klines(bars)
        self.repository.finish_run("run-1", STARTED_AT, "PARTIAL", 1, 3, "ETHUSDT/1h")

        result = MarketScorer(self.repository).score_run("run-1", ("BTCUSDT",))

        self.assertEqual("PARTIAL", self.repository.load_run_quality("run-1"))
        self.assertEqual("PARTIAL", result.ranked_scores[0].data_quality_status)
        with closing(sqlite3.connect(self.database)) as connection:
            stored = connection.execute(
                "SELECT data_quality_status FROM scores WHERE run_id='run-1'"
            ).fetchone()[0]
        self.assertEqual("PARTIAL", stored)

    def test_stale_capital_fallback_is_explicit_on_signal(self) -> None:
        self.repository.start_run("run-1", STARTED_AT)
        self.repository.finish_run("run-1", STARTED_AT, "SUCCEEDED", 1, 3, None)
        self.repository.save_universe("run-1", (member("BTCUSDT"),), STARTED_AT)
        self.repository.save_scores(
            "run-1", (SymbolScore("BTCUSDT", 95, {"trend": {"score": 95}}, "v1"),),
            STARTED_AT,
        )
        snapshot = self.repository.load_snapshot_for_run("run-1")
        self.repository.save_market_regime(
            MarketRegime("BULL", "BULL", "BULL"), STARTED_AT, snapshot.snapshot_id
        )
        for bars in signal_klines("BTCUSDT").values():
            self.repository.save_klines(bars)
        stale = (
            ("OPEN_INTEREST", T - 3 * HOUR, "120"),
            ("OPEN_INTEREST", T - 5 * HOUR, "112"),
            ("OPEN_INTEREST", T - 25 * HOUR, "100"),
            ("FUNDING_RATE", T - 13 * HOUR, "0.0001"),
            ("LONG_SHORT_RATIO", T - 3 * HOUR, "1.1"),
            ("QUOTE_VOLUME_24H", T - 3 * HOUR, "10000000"),
        )
        self.repository.save_capital_observations(
            tuple(CapitalObservation(
                "BTCUSDT", metric, timestamp, Decimal(value), snapshot.snapshot_id
            ) for metric, timestamp, value in stale),
            STARTED_AT,
        )
        self.assertEqual(
            "STALE", CapitalFlowHistory(self.repository).assess_at(
                "run-1", "BTCUSDT", T
            ).data_quality_status,
        )

        signal = SignalGenerator(self.repository).generate_latest(snapshot.snapshot_id).signals[0]

        self.assertEqual(50.0, signal.capital_score)
        self.assertEqual("STALE", signal.data_quality["capital"])
        self.assertEqual("FALLBACK", signal.data_quality["capital_value"])
        self.assertEqual("MISSING", signal.data_quality["space"])
        self.assertEqual("FALLBACK", signal.data_quality["space_value"])
        with closing(sqlite3.connect(self.database)) as connection:
            status, context = connection.execute(
                "SELECT data_quality_status,data_quality_json FROM signals"
            ).fetchone()
        self.assertEqual(signal.data_quality_status, status)
        self.assertEqual(signal.data_quality, json.loads(context))

    def test_all_runtime_result_tables_expose_quality_status(self) -> None:
        expected = {
            "collection_runs", "scores", "capital_snapshots", "space_snapshots",
            "signals", "market_regimes", "sector_snapshots", "backtest_results",
        }
        with closing(sqlite3.connect(self.database)) as connection:
            actual = {
                table for table in expected
                if "data_quality_status" in {
                    row[1] for row in connection.execute(f"PRAGMA table_info({table})")
                }
            }
            signal_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(signals)")
            }
            backtest_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(backtest_results)")
            }
        self.assertEqual(expected, actual)
        self.assertIn("data_quality_json", signal_columns)
        self.assertIn("data_quality_json", backtest_columns)

    def test_database_rejects_unknown_quality_status(self) -> None:
        self.repository.start_run("run-1", STARTED_AT)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repository._connection:
                self.repository._connection.execute(
                    "UPDATE collection_runs SET data_quality_status='UNKNOWN' WHERE id='run-1'"
                )

    def test_capital_space_and_backtest_quality_are_persisted(self) -> None:
        self.repository.start_run("run-1", STARTED_AT)
        self.repository.save_capital_snapshots((CapitalSnapshot(
            "run-1", "BTCUSDT", Decimal("100"), Decimal("1"), Decimal("2"),
            Decimal("3"), Decimal("0.0001"), Decimal("100"), Decimal("1"),
            Decimal("100"), Decimal("50"), Decimal("50"), Decimal("65"), "PARTIAL",
        ),), STARTED_AT)
        common = (Decimal("10"), Decimal("15"), Decimal("20"), Decimal("8"),
                  Decimal("12"), Decimal("18"))
        self.repository.save_space_snapshots((SpaceSnapshot(
            "run-1", "BTCUSDT", "LONG", *common, Decimal("20"), Decimal("18"),
            Decimal("90"), "STALE",
        ),), STARTED_AT)
        self.repository.start_backtest_run("bt-1", STARTED_AT, None, None, 1)
        self.repository.save_backtest_results("bt-1", (BacktestResult(
            evaluation_time_ms=T, symbol="BTCUSDT", direction="LONG",
            combined_regime="BULL", sector="LAYER1", sector_rank=1, score=90,
            entry=Decimal("100"), stop_loss=Decimal("95"), tp1=Decimal("105"),
            tp2=Decimal("110"), rr_tp1=Decimal("1"), rr_tp2=Decimal("2"),
            result="WIN_TP2", bars_to_result=2, realized_r=Decimal("2"),
            data_quality_status="FALLBACK", data_quality={"capital": "FALLBACK"},
        ),))

        with closing(sqlite3.connect(self.database)) as connection:
            capital = connection.execute(
                "SELECT data_quality_status FROM capital_snapshots"
            ).fetchone()[0]
            space = connection.execute(
                "SELECT data_quality_status FROM space_snapshots"
            ).fetchone()[0]
            backtest = connection.execute(
                "SELECT data_quality_status,data_quality_json FROM backtest_results"
            ).fetchone()
        self.assertEqual("PARTIAL", capital)
        self.assertEqual("STALE", space)
        self.assertEqual(("FALLBACK", '{"capital":"FALLBACK"}'), backtest)


if __name__ == "__main__":
    unittest.main()
