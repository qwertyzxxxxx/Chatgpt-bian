from pathlib import Path
import tempfile
import unittest

from binance_ai_trader.domain.models import BacktestMetrics, BacktestSummary
from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.walk_forward import (
    WalkForwardPolicy,
    WalkForwardValidator,
    build_windows,
    identify_overfitting_risks,
    render_markdown,
)


class FakeRepository:
    def __init__(self, points: tuple[int, ...]) -> None:
        self.points = points
        self.maximum_window = None

    def load_backtest_evaluation_times(self, start_ms, end_ms, maximum_window):
        self.maximum_window = maximum_window
        return tuple(
            point for point in self.points
            if (start_ms is None or point >= start_ms) and (end_ms is None or point <= end_ms)
        )


class WalkForwardValidationTest(unittest.TestCase):
    def test_builds_rolling_non_overlapping_partitions(self) -> None:
        windows = build_windows(
            tuple(range(14)),
            WalkForwardPolicy(train_points=4, validation_points=2, test_points=2, step_points=2, embargo_points=1),
        )

        self.assertEqual(3, len(windows))
        self.assertEqual((0, 1, 2, 3), windows[0].training)
        self.assertEqual((5, 6), windows[0].validation)
        self.assertEqual((8, 9), windows[0].test)
        self.assertEqual((2, 3, 4, 5), windows[1].training)
        self.assertEqual((4,), windows[0].embargo_after_training)
        self.assertEqual((7,), windows[0].embargo_after_validation)
        for window in windows:
            self.assertFalse(set(window.training) & set(window.validation))
            self.assertFalse(set(window.training) & set(window.test))
            self.assertFalse(set(window.validation) & set(window.test))
            self.assertLess(max(window.training), min(window.validation))
            self.assertLess(max(window.validation), min(window.test))

    def test_strategy_selection_only_uses_training_partition(self) -> None:
        repository = FakeRepository(tuple(range(10)))
        calls: list[tuple[str, tuple[int, ...]]] = []
        strategies = (strategy("candidate_a"), strategy("candidate_b"))

        def runner(config, points):
            calls.append((config.strategy_id, points))
            if points == (0, 1, 2, 3):
                expectancy = 0.8 if config.strategy_id == "candidate_a" else 0.2
            else:
                # Candidate B would look better out of sample, but that data cannot select it.
                expectancy = -0.4 if config.strategy_id == "candidate_a" else 2.0
            return summary(metrics(expectancy=expectancy, trades=len(points)))

        report = WalkForwardValidator(
            repository,
            sector_map=None,
            policy=WalkForwardPolicy(4, 2, 2, 2, 1),
            runner=runner,
        ).run(strategies)

        self.assertEqual("candidate_a", report.folds[0].selected_strategy_id)
        self.assertEqual(
            [
                ("candidate_a", (0, 1, 2, 3)),
                ("candidate_b", (0, 1, 2, 3)),
                ("candidate_a", (5, 6)),
                ("candidate_a", (8, 9)),
            ],
            calls,
        )
        self.assertEqual(1, repository.maximum_window)

    def test_embargo_must_cover_strategy_evaluation_window(self) -> None:
        validator = WalkForwardValidator(
            FakeRepository(tuple(range(20))),
            sector_map=None,
            policy=WalkForwardPolicy(4, 2, 2, 2, 0),
            runner=lambda config, points: summary(metrics()),
        )

        with self.assertRaisesRegex(ValueError, "embargo_points must be at least"):
            validator.run((strategy("baseline_v1"),))

    def test_flags_out_of_sample_degradation(self) -> None:
        risks = identify_overfitting_risks(
            metrics(expectancy=0.8, profit_factor=2.0, drawdown=2.0),
            metrics(expectancy=-0.1, profit_factor=0.8, drawdown=3.0),
            metrics(expectancy=-0.3, profit_factor=0.7, drawdown=4.0),
        )

        self.assertIn("positive training expectancy did not survive validation", risks)
        self.assertIn("positive training expectancy did not survive the test set", risks)
        self.assertIn("training profit factor advantage reversed out of sample", risks)
        self.assertIn("test drawdown is more than 50% above training drawdown", risks)

    def test_report_contains_required_partition_metrics(self) -> None:
        repository = FakeRepository(tuple(range(10)))
        validator = WalkForwardValidator(
            repository,
            sector_map=None,
            policy=WalkForwardPolicy(4, 2, 2, 2, 1),
            runner=lambda config, points: summary(metrics(trades=len(points))),
        )
        report = validator.run((strategy("baseline_v1"),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "walk_forward.md"
            render_markdown(report, output)
            text = output.read_text()

        self.assertIn("| Train |", text)
        self.assertIn("| Validation |", text)
        self.assertIn("| Test |", text)
        self.assertIn("Win rate", text)
        self.assertIn("Profit factor", text)
        self.assertIn("Max drawdown", text)
        self.assertIn("Number of trades", text)
        self.assertIn("Overfitting risk", text)

    def test_rejects_insufficient_history(self) -> None:
        with self.assertRaisesRegex(ValueError, "insufficient evaluation points"):
            build_windows(tuple(range(9)), WalkForwardPolicy(4, 2, 2, 2, 1))


def strategy(strategy_id: str) -> StrategyConfig:
    return StrategyConfig(
        strategy_id=strategy_id,
        name=strategy_id,
        description="walk-forward fixture",
        scoring_weights={
            "trend": 30.0,
            "volume": 20.0,
            "momentum": 20.0,
            "structure": 15.0,
            "risk": 15.0,
        },
        range_min_score=85.0,
        sector_medium_min_score=85.0,
        sector_weak_min_score=90.0,
        entry_distance_min_pct=-3.0,
        entry_distance_max_pct=1.0,
        max_stop_loss_pct=6.0,
        min_rr_tp2=2.0,
        evaluation_window_bars=1,
    )


def metrics(
    expectancy: float = 0.2,
    profit_factor: float | None = 1.2,
    drawdown: float = 1.0,
    trades: int = 10,
) -> BacktestMetrics:
    return BacktestMetrics(
        total_signals=trades,
        tp1_hit_rate=60.0,
        tp2_win_rate=40.0,
        loss_rate=30.0,
        expired_rate=10.0,
        profit_factor=profit_factor,
        expectancy_r=expectancy,
        max_drawdown_r=drawdown,
        avg_rr_tp2=2.0,
    )


def summary(value: BacktestMetrics) -> BacktestSummary:
    return BacktestSummary(
        run_id="fixture",
        started_at="2026-06-08T00:00:00+00:00",
        completed_at="2026-06-08T00:01:00+00:00",
        evaluation_points=1,
        metrics=value,
        by_direction={},
        by_combined_regime={},
        by_sector={},
        by_score_bucket={},
        by_capital_bucket={},
        by_space_bucket={},
    )


if __name__ == "__main__":
    unittest.main()
