from contextlib import closing, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.analyze_market_regime import MarketRegimeAnalyzer
from binance_ai_trader.entrypoints.cli import main
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from tests.unit.test_regime_engine import regime_klines


class MarketRegimeIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "market.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def seed(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            for symbol in ("BTCUSDT", "ETHUSDT"):
                for items in regime_klines(symbol, "bull").values():
                    repository.save_klines(items)
        finally:
            repository.close()

    def test_analyzes_and_persists_market_regime(self) -> None:
        self.seed()
        repository = MarketDataRepository(self.database)
        try:
            regime = MarketRegimeAnalyzer(repository).analyze()
        finally:
            repository.close()

        self.assertEqual(("BULL", "BULL", "BULL"), (regime.btc_regime, regime.eth_regime, regime.combined_regime))
        with closing(sqlite3.connect(self.database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(market_regimes)")}
            row = connection.execute(
                "SELECT btc_regime, eth_regime, combined_regime, evaluated_at FROM market_regimes"
            ).fetchone()
        self.assertTrue({"btc_regime", "eth_regime", "combined_regime", "evaluated_at"}.issubset(columns))
        self.assertEqual(("BULL", "BULL", "BULL"), row[:3])
        self.assertTrue(row[3])

    def test_regime_cli_outputs_only_market_state(self) -> None:
        self.seed()
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["regime", "--database", str(self.database)])
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(
            {"btc_regime": "BULL", "eth_regime": "BULL", "combined_regime": "BULL"},
            payload,
        )

    def test_missing_market_data_is_observe(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["regime", "--database", str(self.database)])
        self.assertEqual(0, exit_code)
        self.assertEqual(
            {"btc_regime": "OBSERVE", "eth_regime": "OBSERVE", "combined_regime": "OBSERVE"},
            json.loads(output.getvalue()),
        )


if __name__ == "__main__":
    unittest.main()
