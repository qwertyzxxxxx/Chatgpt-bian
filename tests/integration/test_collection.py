from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.collect_market_data import MarketDataCollector
from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.domain.models import Contract, Kline, Ticker24h
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


class FakeClient:
    def exchange_info(self) -> tuple[Contract, ...]:
        return (
            Contract(
                "BTCUSDT", "BTC", "USDT", "USDT", "PERPETUAL", "TRADING",
                2, 3, Decimal("0.10"), Decimal("0.001"),
            ),
        )

    def tickers_24h(self) -> tuple[Ticker24h, ...]:
        return (Ticker24h("BTCUSDT", Decimal("9000000"), Decimal("3.25"), 1710000000000),)

    def klines(self, symbol: str, interval: str, limit: int) -> tuple[Kline, ...]:
        return (
            Kline(
                symbol, interval, 1710000000000, 1710000899999,
                Decimal("100"), Decimal("110"), Decimal("90"), Decimal("105"),
                Decimal("10"), Decimal("1000"), 12,
            ),
        )


class PartiallyFailingClient(FakeClient):
    def klines(self, symbol: str, interval: str, limit: int) -> tuple[Kline, ...]:
        if interval == "1h":
            raise RuntimeError("fixture failure")
        return super().klines(symbol, interval, limit)


class CollectionIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "market.db"
        self.config = UniverseConfig(Decimal("5000000"), frozenset(), tuple(), frozenset())

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_collects_all_intervals_and_persists_required_output(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            result = MarketDataCollector(FakeClient(), repository, self.config, max_workers=2).collect()  # type: ignore[arg-type]
        finally:
            repository.close()

        self.assertEqual(1, len(result.universe))
        self.assertEqual(3, result.kline_count)
        self.assertEqual((), result.failed_requests)
        with closing(sqlite3.connect(self.database)) as connection:
            run = connection.execute("SELECT status, universe_size, kline_count FROM collection_runs").fetchone()
            snapshot = connection.execute(
                "SELECT symbol, volume_24h, change_24h FROM universe_snapshots"
            ).fetchone()
            intervals = connection.execute("SELECT interval FROM klines ORDER BY interval").fetchall()
        self.assertEqual(("SUCCEEDED", 1, 3), run)
        self.assertEqual(("BTCUSDT", "9000000", "3.25"), snapshot)
        self.assertEqual([("15m",), ("1h",), ("4h",)], intervals)

    def test_isolates_one_interval_failure_and_marks_run_partial(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            result = MarketDataCollector(PartiallyFailingClient(), repository, self.config).collect()  # type: ignore[arg-type]
        finally:
            repository.close()

        self.assertEqual(2, result.kline_count)
        self.assertEqual(1, len(result.failed_requests))
        with closing(sqlite3.connect(self.database)) as connection:
            status = connection.execute("SELECT status FROM collection_runs").fetchone()
        self.assertEqual(("PARTIAL",), status)


if __name__ == "__main__":
    unittest.main()
