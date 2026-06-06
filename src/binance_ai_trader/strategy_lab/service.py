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
        self._repository.register_strategy_version(config, "baseline", _utc_now())
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
            ).run(start_ms, end_ms, common_points)
            self._repository.update_strategy_metrics(version.strategy_id, summary.metrics)
            comparisons.append(StrategyComparison(version.strategy_id, summary.metrics))
        return tuple(comparisons)

    def auto_research(
        self,
        maximum_candidates: int = 5,
        start_ms: int | None = None,
        end_ms: int | None = None,
        step_bars: int = 1,
    ) -> tuple[StrategyComparison, ...]:
        if not 1 <= maximum_candidates <= 5:
            raise ValueError("maximum_candidates must be between 1 and 5")
        baseline = self.ensure_baseline()
        candidates = self._candidate_configs(baseline.config)[:maximum_candidates]
        created_at = _utc_now()
        for candidate in candidates:
            self._repository.register_strategy_version(candidate, "candidate", created_at)
        comparisons = self.compare(
            tuple(item.strategy_id for item in candidates), start_ms, end_ms, step_bars
        )
        return tuple(sorted(comparisons, key=_research_rank))

    @staticmethod
    def _candidate_configs(baseline: StrategyConfig) -> tuple[StrategyConfig, ...]:
        batch = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        variants = (
            {
                "name": "Trend emphasis",
                "description": "Research candidate with more trend and less volume weight.",
                "scoring_weights": {
                    "trend": 35.0, "volume": 15.0, "momentum": 20.0,
                    "structure": 15.0, "risk": 15.0,
                },
            },
            {
                "name": "Higher gate quality",
                "description": "Research candidate with stricter range and sector score gates.",
                "range_min_score": min(100.0, baseline.range_min_score + 2.0),
                "sector_medium_min_score": min(100.0, baseline.sector_medium_min_score + 2.0),
                "sector_weak_min_score": min(100.0, baseline.sector_weak_min_score + 2.0),
            },
            {
                "name": "Tighter entry window",
                "description": "Research candidate requiring entries closer to the latest close.",
                "entry_distance_min_pct": max(-10.0, baseline.entry_distance_min_pct + 0.5),
                "entry_distance_max_pct": min(5.0, baseline.entry_distance_max_pct - 0.25),
            },
            {
                "name": "Higher TP2 requirement",
                "description": "Research candidate requiring additional structural reward room.",
                "min_rr_tp2": baseline.min_rr_tp2 + 0.5,
            },
            {
                "name": "Shorter evaluation window",
                "description": "Research candidate evaluated over 72 closed 15m bars.",
                "evaluation_window_bars": min(72, baseline.evaluation_window_bars),
            },
        )
        return tuple(
            replace(
                baseline,
                strategy_id=f"candidate_{batch}_{index}",
                name=str(variant["name"]),
                description=str(variant["description"]),
                **{key: value for key, value in variant.items() if key not in {"name", "description"}},
            )
            for index, variant in enumerate(variants, start=1)
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
