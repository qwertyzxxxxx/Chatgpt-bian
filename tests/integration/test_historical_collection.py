from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.collect_history import HistoricalDataCollector
from binance_ai_trader.config import SectorConfig, UniverseConfig
from binance_ai_trader.domain.models import Contract, Kline, Ticker24h
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


DAY = 86_400_000
INTERVALS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


class FakeHistoricalClient:
    def __init__(self) -> None:
        self.calls = []
        self._contracts = tuple(contract(symbol) for symbol in ("BTCUSDT", "ETHUSDT"))
        self._tickers = (
            Ticker24h("BTCUSDT", Decimal("100000000"), Decimal("1"), 16 * DAY - 1),
            # Below the discovery threshold, but explicitly configured and therefore included.
            Ticker24h("ETHUSDT", Decimal("0"), Decimal("1"), 16 * DAY - 1),
        )

    def exchange_info(self):
        return self._contracts

    def tickers_24h(self):
        return self._tickers

    def historical_klines(
        self, symbol, interval, limit, start_time_ms, end_time_ms, now_ms
    ):
        self.calls.append(("klines", symbol, interval, start_time_ms, end_time_ms))
        step = INTERVALS[interval]
        rows = []
        cursor = ((start_time_ms + step - 1) // step) * step
        while cursor <= end_time_ms and len(rows) < limit:
            close_time = cursor + step - 1
            if close_time > end_time_ms:
                break
            rows.append(Kline(
                symbol, interval, cursor, close_time,
                Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
                Decimal("10"), Decimal("100000"), 10,
            ))
            cursor += step
        return tuple(rows)

    def open_interest_history(self, symbol, limit, start_time_ms, end_time_ms):
        return hourly(start_time_ms, end_time_ms, Decimal("1000"))[:limit]

    def funding_rate_history(self, symbol, limit, start_time_ms, end_time_ms):
        return hourly(start_time_ms, end_time_ms, Decimal("0.0001"), 8)[:limit]

    def global_long_short_ratio_history(self, symbol, limit, start_time_ms, end_time_ms):
        return hourly(start_time_ms, end_time_ms, Decimal("1.1"))[:limit]


class HistoricalCollectionIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "market_data.db"
        self.repository = MarketDataRepository(self.database)
        self.client = FakeHistoricalClient()
        self.collector = HistoricalDataCollector(
            self.client,
            self.repository,
            UniverseConfig(
                minimum_quote_volume_24h=Decimal("1"),
                stablecoin_base_assets=frozenset(),
                leveraged_token_suffixes=("UP", "DOWN"),
                denied_symbols=frozenset(),
            ),
            SectorConfig({"BTCUSDT": "LAYER1", "ETHUSDT": "LAYER1"}),
            request_pause_seconds=0,
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_bootstraps_closed_klines_universes_and_capital_idempotently(self) -> None:
        first = self.collector.collect(days=16, end_ms=16 * DAY - 1)
        with closing(sqlite3.connect(self.database)) as connection:
            initial = counts(connection)
            open_candles = connection.execute(
                "SELECT COUNT(*) FROM klines WHERE close_time_ms>?", (16 * DAY - 1,)
            ).fetchone()[0]
            metrics = {
                row[0] for row in connection.execute(
                    "SELECT DISTINCT metric FROM capital_flow_observations"
                )
            }

        second = self.collector.collect(days=16, end_ms=16 * DAY - 1)
        with closing(sqlite3.connect(self.database)) as connection:
            resumed = counts(connection)
            finalized = connection.execute(
                "SELECT COUNT(*) FROM analysis_snapshots WHERE finalized_at IS NOT NULL"
            ).fetchone()[0]

        self.assertEqual(("BTCUSDT", "ETHUSDT"), first.symbols)
        self.assertEqual(16, first.universe_snapshots)
        btc_15m_calls = [call for call in self.client.calls if call[1:3] == ("BTCUSDT", "15m")]
        self.assertGreaterEqual(len(btc_15m_calls), 2)
        self.assertEqual(0, open_candles)
        self.assertEqual(
            {"OPEN_INTEREST", "FUNDING_RATE", "LONG_SHORT_RATIO", "QUOTE_VOLUME_24H"},
            metrics,
        )
        self.assertEqual(initial[:3], resumed[:3])
        self.assertGreater(resumed[3], initial[3])  # resume has its own immutable ingestion run
        self.assertEqual(0, len(second.failures))
        self.assertGreaterEqual(finalized, 4)
        self.assertTrue(any(call[0] == "klines" for call in self.client.calls))


def counts(connection):
    return (
        connection.execute("SELECT COUNT(*) FROM klines").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM capital_flow_observations").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0],
    )


def contract(symbol: str) -> Contract:
    return Contract(
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        margin_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        price_precision=2,
        quantity_precision=3,
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
    )


def hourly(start_ms, end_ms, value, every_hours=1):
    step = every_hours * 3_600_000
    cursor = ((start_ms + step - 1) // step) * step
    rows = []
    while cursor <= end_ms:
        rows.append((cursor, value))
        cursor += step
    return tuple(rows)


if __name__ == "__main__":
    unittest.main()
