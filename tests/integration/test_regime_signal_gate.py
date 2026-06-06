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

    def generate(self, combined_regime: str | None):
        database = Path(self.tempdir.name) / f"{combined_regime or 'missing'}.db"
        repository = MarketDataRepository(database)
        symbols = ("HIGHUSDT", "LOWUSDT")
        try:
            repository.start_run("run-1", "2026-06-05T00:00:00.000+00:00")
            repository.save_universe(
                "run-1",
                (member(symbol) for symbol in symbols),
                "2026-06-05T00:00:00.000+00:00",
            )
            repository.save_scores(
                "run-1",
                (SymbolScore("HIGHUSDT", 85, {}, "v1"), SymbolScore("LOWUSDT", 79, {}, "v1")),
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
                "SELECT symbol, combined_regime FROM signals ORDER BY rank"
            ).fetchall()
        return result, rows

    def test_bull_allows_normal_signal_generation(self) -> None:
        result, rows = self.generate("BULL")
        self.assertEqual(("HIGHUSDT", "LOWUSDT"), tuple(item.symbol for item in result.signals))
        self.assertEqual([("HIGHUSDT", "BULL"), ("LOWUSDT", "BULL")], rows)

    def test_range_only_allows_scores_at_or_above_80(self) -> None:
        result, rows = self.generate("RANGE")
        self.assertEqual(("HIGHUSDT",), tuple(item.symbol for item in result.signals))
        self.assertEqual([("HIGHUSDT", "RANGE")], rows)

    def test_bear_blocks_all_long_signals(self) -> None:
        result, rows = self.generate("BEAR")
        self.assertEqual((), result.signals)
        self.assertEqual([], rows)

    def test_observe_blocks_all_long_signals(self) -> None:
        result, rows = self.generate("OBSERVE")
        self.assertEqual((), result.signals)
        self.assertEqual([], rows)

    def test_missing_regime_defaults_to_observe_and_blocks_signals(self) -> None:
        result, rows = self.generate(None)
        self.assertEqual((), result.signals)
        self.assertEqual([], rows)


if __name__ == "__main__":
    unittest.main()
