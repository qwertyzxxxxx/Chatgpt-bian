from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from binance_ai_trader.domain.models import BacktestMetrics, BacktestSummary, Kline
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.walk_forward import WalkForwardPolicy, WalkForwardValidator, render_markdown


class WalkForwardIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "walk-forward.db"
        self.repository = MarketDataRepository(self.database)
        for symbol in ("BTCUSDT", "ETHUSDT"):
            self.repository.save_klines(
                Kline(
                    symbol=symbol,
                    interval="15m",
                    open_time_ms=index * 900_000,
                    close_time_ms=(index + 1) * 900_000 - 1,
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100"),
                    volume=Decimal("1"),
                    quote_volume=Decimal("100"),
                    trade_count=1,
                )
                for index in range(11)
            )

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_uses_repository_point_in_time_eligibility_and_writes_report(self) -> None:
        calls = []

        def runner(config, points):
            calls.append(points)
            return BacktestSummary(
                run_id="fixture",
                started_at="start",
                completed_at="end",
                evaluation_points=len(points),
                metrics=BacktestMetrics(
                    total_signals=len(points),
                    tp1_hit_rate=50.0,
                    tp2_win_rate=25.0,
                    loss_rate=25.0,
                    expired_rate=25.0,
                    profit_factor=1.5,
                    expectancy_r=0.25,
                    max_drawdown_r=1.0,
                    avg_rr_tp2=2.0,
                ),
                by_direction={},
                by_combined_regime={},
                by_sector={},
                by_score_bucket={},
                by_capital_bucket={},
                by_space_bucket={},
            )

        report = WalkForwardValidator(
            self.repository,
            sector_map=None,
            policy=WalkForwardPolicy(4, 2, 2, 2, 1),
            runner=runner,
        ).run((baseline(),))
        output = Path(self.tempdir.name) / "report.md"
        render_markdown(report, output)

        self.assertEqual(1, len(report.folds))
        self.assertEqual((4, 2, 2), tuple(len(points) for points in calls))
        self.assertLess(max(calls[0]), min(calls[1]))
        self.assertLess(max(calls[1]), min(calls[2]))
        self.assertIn("| Test | 50.00% | 1.5 | 1.0000 | 2 |", output.read_text())


def baseline() -> StrategyConfig:
    return StrategyConfig.load(Path("config/strategies/baseline_v1.json")).candidate(
        "baseline_fixture", "Baseline fixture", "Short evaluation window fixture",
        evaluation_window_bars=1,
    )


if __name__ == "__main__":
    unittest.main()
