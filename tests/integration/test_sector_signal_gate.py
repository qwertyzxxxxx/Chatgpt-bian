from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.generate_signals import SignalGenerator
from binance_ai_trader.domain.models import MarketRegime, SectorSnapshot, SymbolScore
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap
from tests.integration.test_signals import member
from tests.unit.test_signal_engine import signal_klines


class SectorSignalGateIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def generate(
        self,
        symbol: str,
        score: float,
        sector: str,
        sector_rank: int | None,
    ):
        database = Path(self.tempdir.name) / f"{symbol}-{score}-{sector_rank}.db"
        repository = MarketDataRepository(database)
        try:
            repository.start_run("run-1", "2026-06-05T00:00:00.000+00:00")
            repository.save_universe(
                "run-1",
                (member(symbol),),
                "2026-06-05T00:00:00.000+00:00",
            )
            repository.save_scores(
                "run-1",
                (SymbolScore(symbol, score, {}, "v1"),),
                "2026-06-05T00:00:00.000+00:00",
            )
            repository.save_market_regime(
                MarketRegime("BULL", "BULL", "BULL"),
                "2026-06-05T00:00:01.000+00:00",
            )
            for items in signal_klines(symbol).values():
                repository.save_klines(items)
            if sector_rank is not None:
                repository.save_sector_snapshots(
                    "run-1",
                    (
                        SectorSnapshot(
                            run_id="run-1",
                            sector=sector,
                            sector_rank=sector_rank,
                            member_count=1,
                            avg_score=Decimal(str(score)),
                            median_score=Decimal(str(score)),
                            top3_avg_score=Decimal(str(score)),
                            positive_24h_ratio=Decimal("1"),
                            quote_volume_24h=Decimal("10000000"),
                        ),
                    ),
                    "2026-06-05T00:00:02.000+00:00",
                )
            sector_map = SectorMap({} if sector == "OTHER" else {symbol: sector})
            result = SignalGenerator(repository, sector_map=sector_map).generate_latest()
        finally:
            repository.close()
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute(
                "SELECT symbol, sector, sector_rank FROM signals ORDER BY rank"
            ).fetchall()
        return result, rows

    def test_strong_sector_allows_normal_candidate(self) -> None:
        result, rows = self.generate("STRONGUSDT", 70, "LAYER1", 2)
        self.assertEqual(("STRONGUSDT",), tuple(item.symbol for item in result.signals))
        self.assertEqual([("STRONGUSDT", "LAYER1", 2)], rows)

    def test_medium_sector_requires_score_at_least_85(self) -> None:
        blocked, _ = self.generate("MIDLOWUSDT", 84.99, "DEFI", 4)
        allowed, rows = self.generate("MIDHIGHUSDT", 85, "DEFI", 6)
        self.assertEqual((), blocked.signals)
        self.assertEqual(("MIDHIGHUSDT",), tuple(item.symbol for item in allowed.signals))
        self.assertEqual([("MIDHIGHUSDT", "DEFI", 6)], rows)

    def test_weak_sector_requires_score_at_least_90(self) -> None:
        blocked, _ = self.generate("WEAKLOWUSDT", 89.99, "GAMEFI", 7)
        allowed, rows = self.generate("WEAKHIGHUSDT", 90, "GAMEFI", 8)
        self.assertEqual((), blocked.signals)
        self.assertEqual(("WEAKHIGHUSDT",), tuple(item.symbol for item in allowed.signals))
        self.assertEqual([("WEAKHIGHUSDT", "GAMEFI", 8)], rows)

    def test_other_requires_score_at_least_90(self) -> None:
        blocked, _ = self.generate("OTHERLOWUSDT", 89.99, "OTHER", 1)
        allowed, rows = self.generate("OTHERHIGHUSDT", 90, "OTHER", 1)
        self.assertEqual((), blocked.signals)
        self.assertEqual(("OTHERHIGHUSDT",), tuple(item.symbol for item in allowed.signals))
        self.assertEqual([("OTHERHIGHUSDT", "OTHER", 1)], rows)

    def test_missing_sector_snapshot_does_not_block(self) -> None:
        result, rows = self.generate("NOSNAPSHOTUSDT", 1, "OTHER", None)
        self.assertEqual(("NOSNAPSHOTUSDT",), tuple(item.symbol for item in result.signals))
        self.assertEqual([("NOSNAPSHOTUSDT", "OTHER", None)], rows)

    def test_stronger_sector_is_considered_before_higher_symbol_score(self) -> None:
        database = Path(self.tempdir.name) / "priority.db"
        repository = MarketDataRepository(database)
        try:
            repository.start_run("run-1", "2026-06-05T00:00:00.000+00:00")
            symbols = ("WEAKUSDT", "STRONGUSDT")
            repository.save_universe(
                "run-1",
                (member(symbol) for symbol in symbols),
                "2026-06-05T00:00:00Z",
            )
            repository.save_scores(
                "run-1",
                (SymbolScore("WEAKUSDT", 95, {}, "v1"), SymbolScore("STRONGUSDT", 80, {}, "v1")),
                "2026-06-05T00:00:00Z",
            )
            repository.save_market_regime(
                MarketRegime("BULL", "BULL", "BULL"),
                "2026-06-05T00:00:01Z",
            )
            repository.save_sector_snapshots(
                "run-1",
                (
                    SectorSnapshot(
                        "run-1", "LAYER1", 1, 1, Decimal("80"), Decimal("80"),
                        Decimal("80"), Decimal("1"), Decimal("1"),
                    ),
                    SectorSnapshot(
                        "run-1", "GAMEFI", 7, 1, Decimal("95"), Decimal("95"),
                        Decimal("95"), Decimal("1"), Decimal("1"),
                    ),
                ),
                "2026-06-05T00:00:02Z",
            )
            for symbol in symbols:
                for items in signal_klines(symbol).values():
                    repository.save_klines(items)
            result = SignalGenerator(
                repository,
                sector_map=SectorMap({"STRONGUSDT": "LAYER1", "WEAKUSDT": "GAMEFI"}),
            ).generate_latest()
        finally:
            repository.close()
        self.assertEqual(("STRONGUSDT", "WEAKUSDT"), tuple(item.symbol for item in result.signals))


if __name__ == "__main__":
    unittest.main()
