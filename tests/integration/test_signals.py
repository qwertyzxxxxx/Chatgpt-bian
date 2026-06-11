from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.generate_signals import SignalGenerator
from binance_ai_trader.domain.models import Contract, MarketRegime, SymbolScore, Ticker24h, UniverseMember
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from tests.unit.test_signal_engine import signal_klines


def member(symbol: str) -> UniverseMember:
    return UniverseMember(
        Contract(
            symbol, symbol.removesuffix("USDT"), "USDT", "USDT", "PERPETUAL", "TRADING",
            2, 3, Decimal("0.01"), Decimal("0.001"),
        ),
        Ticker24h(symbol, Decimal("10000000"), Decimal("1"), 1),
    )


class SignalIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "market.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_reads_latest_top20_persists_only_top3_long_signals(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            repository.start_run("old", "2026-06-04T00:00:00.000+00:00")
            repository.save_universe("old", (member("OLDUSDT"),), "2026-06-04T00:00:00.000+00:00")
            repository.save_scores("old", (SymbolScore("OLDUSDT", 99, {}, "v1"),), "2026-06-04T00:00:00.000+00:00")

            repository.start_run("latest", "2026-06-05T00:00:00.000+00:00")
            symbols = tuple(f"COIN{index:02d}USDT" for index in range(1, 22))
            repository.save_universe("latest", (member(symbol) for symbol in symbols), "2026-06-05T00:00:00.000+00:00")
            repository.save_scores(
                "latest",
                (SymbolScore(symbol, 100 - index, {}, "v1") for index, symbol in enumerate(symbols)),
                "2026-06-05T00:00:00.000+00:00",
            )
            repository.save_market_regime(
                MarketRegime("BULL", "BULL", "BULL"),
                "2026-06-05T00:00:01.000+00:00",
            )
            for symbol in (*symbols[:5], symbols[20]):
                for klines in signal_klines(symbol).values():
                    repository.save_klines(klines)

            latest_scores = repository.load_latest_scores(limit=20)
            result = SignalGenerator(repository).generate_latest()
        finally:
            repository.close()

        self.assertEqual("latest", result.run_id)
        self.assertEqual(20, len(latest_scores))
        self.assertNotIn(symbols[20], tuple(item.score.symbol for item in latest_scores))
        self.assertEqual(3, result.processed_symbols)
        self.assertEqual(symbols[:3], tuple(item.symbol for item in result.signals))
        self.assertTrue(all(item.direction == "LONG" for item in result.signals))

        with closing(sqlite3.connect(self.database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(signals)")}
            rows = connection.execute(
                """
                SELECT rank, symbol, direction, combined_regime, score, entry, latest_close,
                       stop_loss, stop_loss_pct, tp1, tp2, rr_tp1, rr_tp2, logic_summary, generated_at
                FROM signals ORDER BY rank
                """
            ).fetchall()
        self.assertTrue(
            {
                "run_id", "rank", "symbol", "direction", "combined_regime", "score",
                "entry", "latest_close",
                "stop_loss", "stop_loss_pct", "tp1", "tp2", "rr_tp1", "rr_tp2",
                "logic_summary", "generated_at",
            }.issubset(columns)
        )
        self.assertEqual(
            [
                (1, symbols[0], "LONG", "BULL"),
                (2, symbols[1], "LONG", "BULL"),
                (3, symbols[2], "LONG", "BULL"),
            ],
            [row[:4] for row in rows],
        )
        self.assertTrue(all(Decimal(row[11]) >= 1 and Decimal(row[12]) >= 2 for row in rows))

    def test_returns_empty_when_scores_do_not_exist(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            result = SignalGenerator(repository).generate_latest()
        finally:
            repository.close()
        self.assertIsNone(result.run_id)
        self.assertEqual((), result.signals)
        self.assertEqual(0, result.processed_symbols)


if __name__ == "__main__":
    unittest.main()
