from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil
from pathlib import Path
from itertools import product

from binance_ai_trader.backtest import BacktestEngine, BacktestPolicy, summarize_results
from binance_ai_trader.domain.models import BacktestResult
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.strategy_lab.models import (
    ChampionStanding,
    StrategyComparison,
    StrategyRanking,
    StrategySweepResult,
    StrategyVersion,
)


PHASE_ONE_STRATEGY_IDS = (
    "baseline_v1",
    "range_disabled_v1",
    "bear_short_space80_v1",
    "capital_60_80_space80_v1",
)
BREAKOUT_HUNTER_SWEEP_COMBINATIONS = 3 * 3 * 4 * 4 * 3 * 2


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
            ).run(start_ms, end_ms, common_points)
            comparison = _comparison_from_results(
                version.strategy_id,
                version.config,
                _with_market_context(
                    self._repository, self._repository.load_backtest_results(summary.run_id)
                ),
                summary.run_id,
                summary.started_at,
                summary.completed_at,
                summary.evaluation_points,
            )
            self._repository.update_strategy_metrics(version.strategy_id, comparison.metrics)
            comparisons.append(comparison)
        return tuple(comparisons)

    def rank(self) -> tuple[StrategyRanking, ...]:
        self.ensure_baseline()
        latest = self._repository.load_latest_successful_backtest()
        if latest is None:
            raise ValueError("no successful backtest results are available")
        run_id, started_at, completed_at, evaluation_points, results = latest
        comparisons = []
        for strategy_id in PHASE_ONE_STRATEGY_IDS:
            version = self._repository.load_strategy_version(strategy_id)
            if version is None:
                raise ValueError(f"unknown strategy: {strategy_id}")
            comparisons.append(
                _comparison_from_results(
                    strategy_id,
                    version.config,
                    results,
                    run_id,
                    started_at,
                    completed_at,
                    evaluation_points,
                )
            )
        ranked = sorted(comparisons, key=_research_rank)
        return tuple(
            StrategyRanking(
                rank=index,
                strategy_id=comparison.strategy_id,
                metrics=comparison.metrics,
                regime_breakdown=comparison.regime_breakdown,
                direction_breakdown=comparison.direction_breakdown,
                verdict=_verdict(comparison),
            )
            for index, comparison in enumerate(ranked, start=1)
        )

    def sweep(self, strategy_id: str) -> tuple[StrategySweepResult, ...]:
        """Rank Breakout Hunter parameter combinations from the latest stored backtest."""
        self.ensure_baseline()
        if strategy_id != "breakout_hunter_v1":
            raise ValueError("parameter sweep is only supported for breakout_hunter_v1")
        version = self._repository.load_strategy_version(strategy_id)
        if version is None:
            raise ValueError(f"unknown strategy: {strategy_id}")
        latest = self._repository.load_latest_successful_backtest()
        if latest is None:
            raise ValueError("no successful backtest results are available")
        run_id, started_at, completed_at, evaluation_points, results = latest
        contextualized = _with_market_context(self._repository, results)
        combinations = []
        for (
            abs_move_percentile,
            quote_volume_min,
            capital_score_min,
            space_score_min,
            max_stop_loss_pct,
            min_rr_tp2,
        ) in product(
            (0.70, 0.80, 0.90),
            (5_000_000.0, 10_000_000.0, 30_000_000.0),
            (50.0, 60.0, 70.0, 80.0),
            (60.0, 70.0, 80.0, 90.0),
            (3.0, 5.0, 7.0),
            (2.0, 3.0),
        ):
            parameters = {
                "abs_move_percentile": abs_move_percentile,
                "quote_volume_min": quote_volume_min,
                "capital_score_min": capital_score_min,
                "space_score_min": space_score_min,
                "max_stop_loss_pct": max_stop_loss_pct,
                "min_rr_tp2": min_rr_tp2,
            }
            config = replace(
                version.config,
                absolute_move_top_percent=(1.0 - abs_move_percentile) * 100.0,
                quote_volume_min=quote_volume_min,
                capital_score_min=capital_score_min,
                space_score_min=space_score_min,
                max_stop_loss_pct=max_stop_loss_pct,
                min_rr_tp2=min_rr_tp2,
            )
            comparison = _comparison_from_results(
                strategy_id,
                config,
                contextualized,
                run_id,
                started_at,
                completed_at,
                evaluation_points,
            )
            combinations.append((parameters, comparison))
        ranked = sorted(
            combinations,
            key=_sweep_rank,
        )[:10]
        return tuple(
            StrategySweepResult(
                rank=index,
                parameters=parameters,
                metrics=comparison.metrics,
                verdict=_verdict(comparison),
            )
            for index, (parameters, comparison) in enumerate(ranked, start=1)
        )

    def champion_league(self) -> tuple[ChampionStanding, ...]:
        """Score all registered strategies against the latest persisted backtest."""
        versions = self.list_versions()
        latest = self._repository.load_latest_successful_backtest()
        if latest is None:
            raise ValueError("no successful backtest results are available")
        run_id, started_at, completed_at, evaluation_points, results = latest
        contextualized = _with_market_context(self._repository, results)
        comparisons = tuple(
            _comparison_from_results(
                version.strategy_id,
                version.config,
                contextualized,
                run_id,
                started_at,
                completed_at,
                evaluation_points,
            )
            for version in versions
        )
        scores = _champion_scores(comparisons)
        ranked = sorted(
            comparisons,
            key=lambda item: (-scores[item.strategy_id], item.strategy_id),
        )[:10]
        return tuple(
            ChampionStanding(
                rank=index,
                strategy_id=comparison.strategy_id,
                score=round(scores[comparison.strategy_id], 6),
                metrics=comparison.metrics,
                verdict=_verdict(comparison),
            )
            for index, comparison in enumerate(ranked, start=1)
        )

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
            ).run(start_ms, end_ms, common_points)
            researched.append(
                (
                    candidate,
                    _comparison_from_results(
                        candidate.strategy_id,
                        candidate,
                        _with_market_context(
                            self._repository,
                            self._repository.load_backtest_results(summary.run_id),
                        ),
                        summary.run_id,
                        summary.started_at,
                        summary.completed_at,
                        summary.evaluation_points,
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


def _comparison_from_results(
    strategy_id: str,
    config: StrategyConfig,
    results: tuple[BacktestResult, ...],
    run_id: str,
    started_at: str,
    completed_at: str,
    evaluation_points: int,
) -> StrategyComparison:
    filtered = _research_results(config, results)
    summary = summarize_results(
        run_id, started_at, completed_at, evaluation_points, filtered
    )
    return StrategyComparison(
        strategy_id,
        summary.metrics,
        summary.by_combined_regime,
        summary.by_direction,
    )


def _research_results(
    config: StrategyConfig, results: tuple[BacktestResult, ...]
) -> tuple[BacktestResult, ...]:
    """Apply deterministic result-level research filters and per-snapshot Top-N selection."""
    filtered = [item for item in results if config.includes_result(item)]
    if config.absolute_move_top_percent < 100:
        selected: list[BacktestResult] = []
        evaluation_times = sorted({item.evaluation_time_ms for item in filtered})
        for evaluation_time in evaluation_times:
            cohort = [
                item for item in filtered if item.evaluation_time_ms == evaluation_time
            ]
            ranked = sorted(cohort, key=_absolute_move, reverse=True)
            keep = max(1, ceil(len(ranked) * config.absolute_move_top_percent / 100))
            selected.extend(ranked[:keep])
        filtered = selected
    if config.quote_volume_min > 0:
        filtered = [
            item
            for item in filtered
            if float(getattr(item, "quote_volume_24h", Decimal("0")))
            >= config.quote_volume_min
        ]
    if config.output_limit is not None:
        selected = []
        evaluation_times = sorted({item.evaluation_time_ms for item in filtered})
        for evaluation_time in evaluation_times:
            cohort = [
                item for item in filtered if item.evaluation_time_ms == evaluation_time
            ]
            selected.extend(
                sorted(cohort, key=_absolute_move, reverse=True)[: config.output_limit]
            )
        filtered = selected
    return tuple(filtered)


def _with_market_context(
    repository: MarketDataRepository, results: tuple[BacktestResult, ...]
) -> tuple[BacktestResult, ...]:
    contextualized = []
    for item in results:
        window = repository.load_klines_at(
            item.symbol, "15m", item.evaluation_time_ms, 96
        )
        if not window:
            contextualized.append(item)
            continue
        change = Decimal("0")
        if len(window) >= 2 and window[0].open > 0:
            change = (
                (window[-1].close - window[0].open)
                / window[0].open
                * Decimal("100")
            )
        contextualized.append(
            replace(
                item,
                change_24h=change,
                quote_volume_24h=sum(
                    (bar.quote_volume for bar in window), Decimal("0")
                ),
            )
        )
    return tuple(contextualized)


def _absolute_move(result: BacktestResult) -> float:
    return abs(float(getattr(result, "change_24h", 0.0)))


def _verdict(item: StrategyComparison) -> str:
    metrics = item.metrics
    if (
        metrics.total_signals == 0
        or metrics.expectancy_r <= 0
        or (metrics.profit_factor is not None and metrics.profit_factor < 1.0)
    ):
        return "REJECT"
    if (
        metrics.total_signals >= 20
        and (metrics.profit_factor is None or metrics.profit_factor >= 1.2)
        and metrics.max_drawdown_r <= 10.0
    ):
        return "PASS"
    return "WATCH"


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


def _sweep_rank(
    item: tuple[dict[str, float], StrategyComparison]
) -> tuple[float, float, float, int, tuple[float, ...]]:
    parameters, comparison = item
    research_rank = _research_rank(comparison)
    return (
        research_rank[0],
        research_rank[1],
        research_rank[2],
        -comparison.metrics.total_signals,
        tuple(parameters.values()),
    )


def _champion_scores(
    comparisons: tuple[StrategyComparison, ...],
) -> dict[str, float]:
    if not comparisons:
        return {}
    profit_factors = tuple(_finite_profit_factor(item) for item in comparisons)
    expectancies = tuple(item.metrics.expectancy_r for item in comparisons)
    drawdowns = tuple(item.metrics.max_drawdown_r for item in comparisons)
    trades = tuple(float(item.metrics.total_signals) for item in comparisons)
    return {
        item.strategy_id: (
            0.40 * _normalized(_finite_profit_factor(item), profit_factors)
            + 0.30 * _normalized(item.metrics.expectancy_r, expectancies)
            + 0.20 * (1.0 - _normalized(item.metrics.max_drawdown_r, drawdowns))
            + 0.10 * _normalized(float(item.metrics.total_signals), trades)
        )
        for item in comparisons
    }


def _finite_profit_factor(item: StrategyComparison) -> float:
    value = item.metrics.profit_factor
    if value is not None:
        return value
    return 1_000_000.0 if item.metrics.total_signals > 0 else 0.0


def _normalized(value: float, values: tuple[float, ...]) -> float:
    low = min(values)
    high = max(values)
    if high == low:
        return 1.0
    return (value - low) / (high - low)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
