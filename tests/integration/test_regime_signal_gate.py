from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.generate_signals import SignalGenerator
from binance_ai_trader.domain.models import MarketRegime, SymbolScore
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from tests.integration.test_signals import member
from tests.unit.test_signal_engine import signal_klines


class RegimeSignalGateIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def generate(self, combined_regime: str | None, scores=(85, 79)):
        database = Path(self.tempdir.name) / f"{combined_regime or 'missing'}-{scores}.db"
        repository = MarketDataRepository(database)
        symbols = ("HIGHUSDT", "LOWUSDT")
        try:
            repository.start_run("run-1", "2026-06-05T00:00:00.000+00:00")
            repository.save_universe(
                "run-1", (member(symbol) for symbol in symbols),
                "2026-06-05T00:00:00.000+00:00",
            )
            repository.save_scores(
                "run-1",
                tuple(SymbolScore(symbol, score, {}, "v1") for symbol, score in zip(symbols, scores)),
                "2026-06-05T00:00:00.000+00:00",
            )
            for symbol in symbols:
                for items in signal_klines(symbol).values():
                    repository.save_klines(items)
            if combined_regime is not None:
                repository.save_market_regime(
                    MarketRegime(combined_regime, combined_regime, combined_regime),
                    "2026-06-05T00:00:01.000+00:00",
                )
            result = SignalGenerator(repository).generate_latest()
        finally:
            repository.close()
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute(
                "SELECT symbol, direction, score, combined_regime FROM signals ORDER BY rank"
            ).fetchall()
        return result, rows

    def test_bull_allows_only_long_signals(self) -> None:
        result, rows = self.generate("BULL")
        self.assertEqual(("LONG", "LONG"), tuple(item.direction for item in result.signals))
        self.assertEqual([("HIGHUSDT", "LONG", 85.0, "BULL"), ("LOWUSDT", "LONG", 79.0, "BULL")], rows)

    def test_bear_generates_short_signals_from_weakest_candidates_first(self) -> None:
        result, rows = self.generate("BEAR")
        self.assertEqual(("SHORT", "SHORT"), tuple(item.direction for item in result.signals))
        self.assertEqual(("LOWUSDT", "HIGHUSDT"), tuple(item.symbol for item in result.signals))
        self.assertTrue(all(item.combined_regime == "BEAR" for item in result.signals))
        self.assertTrue(all(item.sector == "OTHER" and item.sector_rank is None for item in result.signals))
        self.assertEqual([("LOWUSDT", "SHORT", 21.0, "BEAR"), ("HIGHUSDT", "SHORT", 15.0, "BEAR")], rows)

    def test_range_allows_only_long_and_short_strength_at_least_85(self) -> None:
        result, rows = self.generate("RANGE", scores=(90, 10))
        self.assertEqual({("HIGHUSDT", "LONG"), ("LOWUSDT", "SHORT")}, {(item.symbol, item.direction) for item in result.signals})
        self.assertTrue(all(item.score >= 85 for item in result.signals))
        self.assertEqual({("HIGHUSDT", "LONG", 90.0, "RANGE"), ("LOWUSDT", "SHORT", 90.0, "RANGE")}, set(rows))

    def test_observe_blocks_all_signals(self) -> None:
        result, rows = self.generate("OBSERVE")
        self.assertEqual((), result.signals)
        self.assertEqual([], rows)

    def test_missing_regime_defaults_to_observe_and_blocks_signals(self) -> None:
        result, rows = self.generate(None)
        self.assertEqual((), result.signals)
        self.assertEqual([], rows)


if __name__ == "__main__":
    unittest.main()
