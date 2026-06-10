from contextlib import closing, redirect_stdout
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.application.analyze_sector_strength import SectorStrengthAnalyzer
from binance_ai_trader.domain.models import Contract, SymbolScore, Ticker24h, UniverseMember
from binance_ai_trader.entrypoints.cli import main
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap


def member(symbol: str, volume: str, change: str) -> UniverseMember:
    return UniverseMember(
        Contract(
            symbol=symbol,
            base_asset=symbol.removesuffix("USDT"),
            quote_asset="USDT",
            margin_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            price_precision=2,
            quantity_precision=3,
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
        ),
        Ticker24h(symbol, Decimal(volume), Decimal(change), 1),
    )


class SectorStrengthIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "market.db"
        self.config = Path(self.tempdir.name) / "sectors.json"
        self.config.write_text(
            json.dumps({"symbol_to_sector": {"FETUSDT": "AI_AGENT", "DOGEUSDT": "MEME"}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def seed(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            repository.start_run("old", "2026-06-04T00:00:00.000+00:00")
            repository.save_universe("old", (member("OLDUSDT", "100", "1"),), "2026-06-04T00:00:00Z")
            repository.save_scores("old", (SymbolScore("OLDUSDT", 99, {}, "v1"),), "2026-06-04T00:00:00Z")

            repository.start_run("latest", "2026-06-05T00:00:00.000+00:00")
            members = (
                member("FETUSDT", "1000", "5"),
                member("DOGEUSDT", "2000", "-2"),
                member("NEWUSDT", "500", "1"),
            )
            repository.save_universe("latest", members, "2026-06-05T00:00:00Z")
            repository.save_scores(
                "latest",
                (
                    SymbolScore("FETUSDT", 90, {}, "v1"),
                    SymbolScore("DOGEUSDT", 80, {}, "v1"),
                    SymbolScore("NEWUSDT", 70, {}, "v1"),
                ),
                "2026-06-05T00:00:00Z",
            )
        finally:
            repository.close()

    def test_reads_latest_run_calculates_and_persists_ranked_sectors(self) -> None:
        self.seed()
        repository = MarketDataRepository(self.database)
        try:
            analyzer = SectorStrengthAnalyzer(
                repository,
                SectorMap({"FETUSDT": "AI_AGENT", "DOGEUSDT": "MEME"}),
            )
            snapshots = analyzer.analyze_latest()
            analyzer.analyze_latest()
        finally:
            repository.close()

        self.assertEqual(("AI_AGENT", "MEME", "OTHER"), tuple(item.sector for item in snapshots))
        with closing(sqlite3.connect(self.database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sector_snapshots)")}
            rows = connection.execute(
                """
                SELECT run_id, sector, sector_rank, member_count, avg_score, median_score,
                       top3_avg_score, positive_24h_ratio, quote_volume_24h, calculated_at
                FROM sector_snapshots ORDER BY sector_rank
                """
            ).fetchall()
        self.assertEqual(3, len(rows))
        self.assertTrue(all(row[0] == "latest" for row in rows))
        self.assertEqual(("AI_AGENT", 1, 1, "90.00", "90.00", "90.00", "1.0000", "1000"), rows[0][1:9])
        self.assertTrue(
            {
                "run_id", "sector", "sector_rank", "member_count", "avg_score",
                "median_score", "top3_avg_score", "positive_24h_ratio",
                "quote_volume_24h", "calculated_at",
            }.issubset(columns)
        )

    def test_sectors_cli_outputs_ranked_json_lines(self) -> None:
        self.seed()
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                ["sectors", "--database", str(self.database), "--config", str(self.config)]
            )
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(0, exit_code)
        self.assertEqual(["AI_AGENT", "MEME", "OTHER"], [line["sector"] for line in lines])
        self.assertEqual(list(range(1, 4)), [line["sector_rank"] for line in lines])
        self.assertEqual(
            {
                "sector", "sector_rank", "member_count", "avg_score", "median_score",
                "top3_avg_score", "positive_24h_ratio", "quote_volume_24h",
                "data_quality_status",
            },
            set(lines[0]),
        )

    def test_returns_no_rows_when_scores_do_not_exist(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                ["sectors", "--database", str(self.database), "--config", str(self.config)]
            )
        self.assertEqual(0, exit_code)
        self.assertEqual("", output.getvalue())


if __name__ == "__main__":
    unittest.main()
