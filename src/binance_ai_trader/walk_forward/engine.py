from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from binance_ai_trader.backtest import BacktestEngine, BacktestPolicy
from binance_ai_trader.domain.models import BacktestMetrics, BacktestSummary
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.strategy_lab.config import StrategyConfig


BacktestRunner = Callable[[StrategyConfig, tuple[int, ...]], BacktestSummary]


@dataclass(frozen=True, slots=True)
class WalkForwardPolicy:
    train_points: int = 720
    validation_points: int = 240
    test_points: int = 240
    step_points: int = 240
    embargo_points: int = 96

    def __post_init__(self) -> None:
        for name, value in (
            ("train_points", self.train_points),
            ("validation_points", self.validation_points),
            ("test_points", self.test_points),
            ("step_points", self.step_points),
        ):
            if value < 1:
                raise ValueError(f"{name} must be positive")
        if self.embargo_points < 0:
            raise ValueError("embargo_points cannot be negative")


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    index: int
    training: tuple[int, ...]
    validation: tuple[int, ...]
    test: tuple[int, ...]
    embargo_after_training: tuple[int, ...] = ()
    embargo_after_validation: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    window: WalkForwardWindow
    selected_strategy_id: str
    train_metrics: BacktestMetrics
    validation_metrics: BacktestMetrics
    test_metrics: BacktestMetrics
    overfitting_risks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    folds: tuple[WalkForwardFold, ...]
    candidate_strategy_ids: tuple[str, ...]

    @property
    def overfitting_risks(self) -> tuple[str, ...]:
        return tuple(
            f"window {fold.window.index}: {risk}"
            for fold in self.folds
            for risk in fold.overfitting_risks
        )


class WalkForwardValidator:
    """Rolling train/validation/test evaluation with training-only selection."""

    def __init__(
        self,
        repository: MarketDataRepository,
        sector_map: SectorMap,
        policy: WalkForwardPolicy | None = None,
        runner: BacktestRunner | None = None,
    ) -> None:
        self._repository = repository
        self._sector_map = sector_map
        self._policy = policy or WalkForwardPolicy()
        self._runner = runner or self._run_backtest

    def run(
        self,
        strategies: Sequence[StrategyConfig],
        start_ms: int | None = None,
        end_ms: int | None = None,
        point_stride: int = 1,
    ) -> WalkForwardReport:
        if not strategies:
            raise ValueError("at least one strategy is required")
        if point_stride < 1:
            raise ValueError("point_stride must be positive")
        strategy_ids = tuple(item.strategy_id for item in strategies)
        if len(set(strategy_ids)) != len(strategy_ids):
            raise ValueError("strategy ids must be unique")

        maximum_window = max(item.evaluation_window_bars for item in strategies)
        if self._policy.embargo_points < maximum_window:
            raise ValueError(
                "embargo_points must be at least the maximum evaluation window "
                f"({maximum_window})"
            )
        common_points = self._repository.load_backtest_evaluation_times(
            start_ms, end_ms, maximum_window
        )[::point_stride]
        windows = build_windows(common_points, self._policy)
        folds = []
        for window in windows:
            # Candidate selection is intentionally limited to the training slice.
            training_runs = tuple(
                (strategy, self._runner(strategy, window.training)) for strategy in strategies
            )
            selected, training_summary = min(training_runs, key=_training_rank)
            validation_summary = self._runner(selected, window.validation)
            test_summary = self._runner(selected, window.test)
            folds.append(
                WalkForwardFold(
                    window=window,
                    selected_strategy_id=selected.strategy_id,
                    train_metrics=training_summary.metrics,
                    validation_metrics=validation_summary.metrics,
                    test_metrics=test_summary.metrics,
                    overfitting_risks=identify_overfitting_risks(
                        training_summary.metrics,
                        validation_summary.metrics,
                        test_summary.metrics,
                    ),
                )
            )
        return WalkForwardReport(tuple(folds), strategy_ids)

    def _run_backtest(
        self, strategy: StrategyConfig, evaluation_times: tuple[int, ...]
    ) -> BacktestSummary:
        return BacktestEngine(
            self._repository,
            self._sector_map,
            BacktestPolicy(maximum_evaluation_bars=strategy.evaluation_window_bars),
            strategy_config=strategy,
        ).run(evaluation_times=evaluation_times)


def build_windows(
    evaluation_times: Sequence[int], policy: WalkForwardPolicy
) -> tuple[WalkForwardWindow, ...]:
    points = tuple(sorted(set(evaluation_times)))
    window_size = (
        policy.train_points + policy.validation_points + policy.test_points
        + 2 * policy.embargo_points
    )
    windows = []
    for start in range(0, len(points) - window_size + 1, policy.step_points):
        train_end = start + policy.train_points
        validation_start = train_end + policy.embargo_points
        validation_end = validation_start + policy.validation_points
        test_start = validation_end + policy.embargo_points
        test_end = test_start + policy.test_points
        windows.append(
            WalkForwardWindow(
                index=len(windows) + 1,
                training=points[start:train_end],
                validation=points[validation_start:validation_end],
                test=points[test_start:test_end],
                embargo_after_training=points[train_end:validation_start],
                embargo_after_validation=points[validation_end:test_start],
            )
        )
    if not windows:
        raise ValueError(
            f"insufficient evaluation points: need at least {window_size}, got {len(points)}"
        )
    return tuple(windows)


def identify_overfitting_risks(
    train: BacktestMetrics, validation: BacktestMetrics, test: BacktestMetrics
) -> tuple[str, ...]:
    risks = []
    if min(train.total_signals, validation.total_signals, test.total_signals) == 0:
        risks.append("one or more partitions contain no trades")
    if train.expectancy_r > 0 and validation.expectancy_r <= 0:
        risks.append("positive training expectancy did not survive validation")
    if train.expectancy_r > 0 and test.expectancy_r <= 0:
        risks.append("positive training expectancy did not survive the test set")
    if train.expectancy_r > 0 and test.expectancy_r < train.expectancy_r * 0.5:
        risks.append("test expectancy is less than half of training expectancy")
    if _profit_factor(train) >= 1.5 and _profit_factor(test) < 1.0:
        risks.append("training profit factor advantage reversed out of sample")
    if test.max_drawdown_r > train.max_drawdown_r * 1.5 and test.max_drawdown_r > 0:
        risks.append("test drawdown is more than 50% above training drawdown")
    return tuple(dict.fromkeys(risks))


def render_markdown(report: WalkForwardReport, output: Path) -> None:
    lines = [
        "# Walk-Forward Validation",
        "",
        "Candidate strategies are selected using **training data only**. Validation and test",
        "partitions are evaluated only after selection and never participate in tuning.",
        "",
        "Win rate is the percentage of trades reaching at least TP1.",
        "",
    ]
    for fold in report.folds:
        window = fold.window
        lines.extend(
            [
                f"## Window {window.index}",
                "",
                f"- Selected strategy: `{fold.selected_strategy_id}`",
                f"- Training range: `{window.training[0]}` to `{window.training[-1]}` ({len(window.training)} points)",
                f"- Purged points after training: {len(window.embargo_after_training)}",
                f"- Validation range: `{window.validation[0]}` to `{window.validation[-1]}` ({len(window.validation)} points)",
                f"- Purged points after validation: {len(window.embargo_after_validation)}",
                f"- Test range: `{window.test[0]}` to `{window.test[-1]}` ({len(window.test)} points)",
                "",
                "| Partition | Win rate | Profit factor | Max drawdown (R) | Number of trades |",
                "| --- | ---: | ---: | ---: | ---: |",
                _metrics_row("Train", fold.train_metrics),
                _metrics_row("Validation", fold.validation_metrics),
                _metrics_row("Test", fold.test_metrics),
                "",
                "### Overfitting risk",
                "",
            ]
        )
        if fold.overfitting_risks:
            lines.extend(f"- ⚠️ {risk}" for risk in fold.overfitting_risks)
        else:
            lines.append("- No automatic warning was triggered; this is not proof of future profitability.")
        lines.append("")
    lines.extend(["## Cross-window warnings", ""])
    if report.overfitting_risks:
        lines.extend(f"- ⚠️ {risk}" for risk in report.overfitting_risks)
    else:
        lines.append("- No automatic warning was triggered across the evaluated windows.")
    lines.extend(
        [
            "",
            "> Walk-forward validation reduces selection bias but cannot eliminate regime change,",
            "> data quality issues, transaction-cost assumptions, or limited sample-size risk.",
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def _training_rank(item: tuple[StrategyConfig, BacktestSummary]) -> tuple[float, float, float, str]:
    strategy, summary = item
    metrics = summary.metrics
    return (
        -metrics.expectancy_r,
        -_profit_factor(metrics),
        metrics.max_drawdown_r,
        strategy.strategy_id,
    )


def _profit_factor(metrics: BacktestMetrics) -> float:
    if metrics.profit_factor is None:
        return float("inf") if metrics.total_signals else 0.0
    return metrics.profit_factor


def _metrics_row(label: str, metrics: BacktestMetrics) -> str:
    if metrics.profit_factor is None and metrics.total_signals:
        profit_factor = "∞"
    else:
        profit_factor = str(metrics.profit_factor or 0.0)
    return (
        f"| {label} | {metrics.tp1_hit_rate:.2f}% | {profit_factor} | "
        f"{metrics.max_drawdown_r:.4f} | {metrics.total_signals} |"
    )
