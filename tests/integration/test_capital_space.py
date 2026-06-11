from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.capital import CapitalSnapshot
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.space import SpaceSnapshot


class CapitalSpacePersistenceTest(unittest.TestCase):
    def test_persists_and_loads_capital_and_directional_space_scores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.db"
            repository = MarketDataRepository(database)
            try:
                repository.start_run("run-1", "2026-06-06T00:00:00.000+00:00")
                repository.save_capital_snapshots((CapitalSnapshot(
                    "run-1", "BTCUSDT", Decimal("100"), Decimal("1"), Decimal("4"),
                    Decimal("12"), Decimal("0.0001"), Decimal("100"), Decimal("1.1"),
                    Decimal("93"), Decimal("75"), Decimal("80"), Decimal("84"),
                ),), "2026-06-06T00:01:00.000+00:00")
                common = (Decimal("10"), Decimal("15"), Decimal("20"),
                          Decimal("8"), Decimal("12"), Decimal("18"))
                repository.save_space_snapshots((
                    SpaceSnapshot("run-1", "BTCUSDT", "LONG", *common,
                                  Decimal("20"), Decimal("18"), Decimal("100")),
                    SpaceSnapshot("run-1", "BTCUSDT", "SHORT", *common,
                                  Decimal("20"), Decimal("18"), Decimal("90")),
                ), "2026-06-06T00:01:00.000+00:00")
                self.assertEqual({"BTCUSDT": 84.0}, repository.load_capital_scores("run-1"))
                self.assertEqual(100.0, repository.load_space_scores("run-1")[("BTCUSDT", "LONG")])
                self.assertEqual(84.0, repository.load_capital_score_at("BTCUSDT", 1780704120000))
            finally:
                repository.close()
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM capital_snapshots").fetchone()[0])
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM space_snapshots").fetchone()[0])
