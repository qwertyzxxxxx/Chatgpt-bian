from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from binance_ai_trader.backtest import BacktestEngine, BacktestPolicy
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.strategy_lab.models import StrategyComparison, StrategyVersion


class StrategyLab:
    """Research-only strategy registry and deterministic parameter comparison."""

    def __init__(
        self,
        repository: MarketDataRepository,
        sector_map: SectorMap,
        baseline_path: Path = Path("config/strategies/baseline_v1.json"),
    ) -> None:
        self._repository = repository
        self._sector_map = sector_map
        self._baseline_path = baseline_path

    def ensure_baseline(self) -> StrategyVersion:
        config = StrategyConfig.load(self._baseline_path)
        created_at = _utc_now()
        self._repository.register_strategy_version(config, "baseline", created_at)
        for path in sorted(self._baseline_path.parent.glob("*.json")):
            if path == self._baseline_path:
                continue
            self._repository.register_strategy_version(
                StrategyConfig.load(path), "candidate", created_at
            )
        version = self._repository.load_strategy_version(config.strategy_id)
        assert version is not None
        return version

    def list_versions(self) -> tuple[StrategyVersion, ...]:
        self.ensure_baseline()
        return self._repository.list_strategy_versions()

    def load_for_manual_run(self, strategy_id: str) -> StrategyConfig:
        self.ensure_baseline()
        version = self._repository.load_strategy_version(strategy_id)
        if version is None:
            raise ValueError(f"unknown strategy: {strategy_id}")
        if version.status not in {"baseline", "approved"}:
            raise ValueError("only baseline or approved strategies may be manually run")
        return version.config

    def compare(
        self,
        strategy_ids: tuple[str, ...],
        start_ms: int | None = None,
        end_ms: int | None = None,
        step_bars: int = 1,
    ) -> tuple[StrategyComparison, ...]:
        self.ensure_baseline()
        if not strategy_ids:
            raise ValueError("at least one strategy_id is required")
        versions = []
        for strategy_id in strategy_ids:
            version = self._repository.load_strategy_version(strategy_id)
            if version is None:
                raise ValueError(f"unknown strategy: {strategy_id}")
            versions.append(version)
        maximum_window = max(item.config.evaluation_window_bars for item in versions)
        common_points = self._repository.load_backtest_evaluation_times(
            start_ms, end_ms, maximum_window
        )
        comparisons = []
        for version in versions:
            summary = BacktestEngine(
                self._repository,
                self._sector_map,
                BacktestPolicy(
                    step_bars=step_bars,
                    maximum_evaluation_bars=version.config.evaluation_window_bars,
                ),
                strategy_config=version.config,
                result_filter=version.config.includes_result,
            ).run(start_ms, end_ms, common_points)
            self._repository.update_strategy_metrics(version.strategy_id, summary.metrics)
            comparisons.append(
                StrategyComparison(
                    version.strategy_id, summary.metrics, summary.by_combined_regime
                )
            )
        return tuple(comparisons)

    def auto_research(
        self,
        maximum_candidates: int = 10,
        start_ms: int | None = None,
        end_ms: int | None = None,
        step_bars: int = 1,
    ) -> tuple[StrategyComparison, ...]:
        if not 1 <= maximum_candidates <= 10:
            raise ValueError("maximum_candidates must be between 1 and 10")
        baseline = self.ensure_baseline()
        candidates = self._candidate_configs(baseline.config)
        maximum_window = max(item.evaluation_window_bars for item in candidates)
        common_points = self._repository.load_backtest_evaluation_times(
            start_ms, end_ms, maximum_window
        )
        researched: list[tuple[StrategyConfig, StrategyComparison]] = []
        for candidate in candidates:
            summary = BacktestEngine(
                self._repository,
                self._sector_map,
                BacktestPolicy(
                    step_bars=step_bars,
                    maximum_evaluation_bars=candidate.evaluation_window_bars,
                ),
                strategy_config=candidate,
                result_filter=candidate.includes_result,
            ).run(start_ms, end_ms, common_points)
            researched.append(
                (
                    candidate,
                    StrategyComparison(
                        candidate.strategy_id, summary.metrics, summary.by_combined_regime
                    ),
                )
            )
        passed = [item for item in researched if _passes_observation_gate(item[1])]
        ranked = sorted(passed, key=lambda item: _research_rank(item[1]))
        top = ranked[:maximum_candidates]
        created_at = _utc_now()
        for candidate, comparison in top:
            self._repository.register_strategy_version(
                candidate, "candidate", created_at, comparison.metrics
            )
        return tuple(comparison for _, comparison in top)

    @staticmethod
    def _candidate_configs(baseline: StrategyConfig) -> tuple[StrategyConfig, ...]:
        batch = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        variants = (
            ("Trend 35", {"scoring_weights": {"trend": 35.0, "volume": 15.0, "momentum": 20.0, "structure": 15.0, "risk": 15.0}}),
            ("Momentum 25", {"scoring_weights": {"trend": 27.5, "volume": 17.5, "momentum": 25.0, "structure": 15.0, "risk": 15.0}}),
            ("Structure 20", {"scoring_weights": {"trend": 27.5, "volume": 17.5, "momentum": 20.0, "structure": 20.0, "risk": 15.0}}),
            ("Risk 20", {"scoring_weights": {"trend": 27.5, "volume": 17.5, "momentum": 20.0, "structure": 15.0, "risk": 20.0}}),
            ("Volume 25", {"scoring_weights": {"trend": 27.5, "volume": 25.0, "momentum": 17.5, "structure": 15.0, "risk": 15.0}}),
            ("Range 87", {"range_min_score": 87.0}),
            ("Range 90", {"range_min_score": 90.0}),
            ("Sector 87 92", {"sector_medium_min_score": 87.0, "sector_weak_min_score": 92.0}),
            ("Sector 90 95", {"sector_medium_min_score": 90.0, "sector_weak_min_score": 95.0}),
            ("Entry tight A", {"entry_distance_min_pct": -2.5, "entry_distance_max_pct": 0.75}),
            ("Entry tight B", {"entry_distance_min_pct": -2.0, "entry_distance_max_pct": 0.5}),
            ("Entry broad", {"entry_distance_min_pct": -4.0, "entry_distance_max_pct": 1.25}),
            ("Stop 6", {"max_stop_loss_pct": 6.0}),
            ("Stop 5", {"max_stop_loss_pct": 5.0}),
            ("RR 2.25", {"min_rr_tp2": 2.25}),
            ("RR 2.5", {"min_rr_tp2": 2.5}),
            ("Window 72", {"evaluation_window_bars": 72}),
            ("Window 48", {"evaluation_window_bars": 48}),
            ("Quality combo", {"range_min_score": 88.0, "sector_medium_min_score": 88.0, "sector_weak_min_score": 93.0, "min_rr_tp2": 2.25}),
            ("Tight combo", {"entry_distance_min_pct": -2.0, "entry_distance_max_pct": 0.5, "max_stop_loss_pct": 6.0, "evaluation_window_bars": 72}),
        )
        return tuple(
            baseline.candidate(
                f"candidate_{batch}_{index:02d}",
                name,
                "Automatically generated parameter-only research candidate.",
                **changes,
            )
            for index, (name, changes) in enumerate(variants, start=1)
        )


def _passes_observation_gate(item: StrategyComparison) -> bool:
    metrics = item.metrics
    return (
        metrics.total_signals > 0
        and metrics.expectancy_r > 0
        and (metrics.profit_factor is None or metrics.profit_factor > 1.0)
    )


def _research_rank(item: StrategyComparison) -> tuple[float, float, float, str]:
    profit_factor = item.metrics.profit_factor
    if profit_factor is None:
        profit_factor = float("inf") if item.metrics.total_signals > 0 else 0.0
    return (
        -item.metrics.expectancy_r,
        -profit_factor,
        item.metrics.max_drawdown_r,
        item.strategy_id,
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
