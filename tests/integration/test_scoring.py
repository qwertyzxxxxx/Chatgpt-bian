from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.score_market_data import MarketScorer
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from tests.unit.test_scoring_engine import market


class ScoringIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "market.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_ranks_symbols_and_persists_scores_with_breakdown(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            repository.start_run("run-1", "2026-06-05T00:00:00.000+00:00")
            for symbol, growth in (("DOWNUSDT", -0.005), ("UPUSDT", 0.005)):
                for klines in market(symbol, growth).values():
                    repository.save_klines(klines)
            result = MarketScorer(repository).score_run("run-1", ("DOWNUSDT", "UPUSDT"))
        finally:
            repository.close()

        self.assertEqual(["UPUSDT", "DOWNUSDT"], [item.symbol for item in result.ranked_scores])
        with closing(sqlite3.connect(self.database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(scores)")}
            rows = connection.execute(
                "SELECT rank, symbol, score, score_breakdown_json, algorithm_version FROM scores ORDER BY rank"
            ).fetchall()
        self.assertTrue({"run_id", "rank", "symbol", "score", "score_breakdown_json"}.issubset(columns))
        self.assertEqual((1, "UPUSDT"), rows[0][:2])
        self.assertEqual("v1", rows[0][4])
        self.assertEqual({"trend", "volume", "momentum", "structure", "risk"}, set(json.loads(rows[0][3])))

    def test_skips_excluded_and_insufficient_symbols(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            repository.start_run("run-2", "2026-06-05T00:00:00.000+00:00")
            for klines in market("GOODUSDT", 0.002).values():
                repository.save_klines(klines)
            result = MarketScorer(repository).score_run(
                "run-2", ("GOODUSDT", "MISSINGUSDT", "FAILEDUSDT"), ("FAILEDUSDT",)
            )
        finally:
            repository.close()

        self.assertEqual(("GOODUSDT",), tuple(item.symbol for item in result.ranked_scores))
        self.assertEqual(("FAILEDUSDT", "MISSINGUSDT"), result.skipped_symbols)
        self.assertEqual("PARTIAL", result.data_quality_status)

    def test_policy_exclusion_does_not_degrade_complete_score_quality(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            repository.start_run("run-3", "2026-06-05T00:00:00.000+00:00")
            for klines in market("GOODUSDT", 0.002).values():
                repository.save_klines(klines)
            repository.finish_run(
                "run-3", "2026-06-05T00:01:00.000+00:00", "SUCCEEDED", 2, 600, None
            )
            result = MarketScorer(repository).score_run(
                "run-3", ("GOODUSDT", "EXCLUDEDUSDT"), ("EXCLUDEDUSDT",)
            )
        finally:
            repository.close()

        self.assertEqual("COMPLETE", result.data_quality_status)
        self.assertEqual(("EXCLUDEDUSDT",), result.skipped_symbols)


if __name__ == "__main__":
    unittest.main()
