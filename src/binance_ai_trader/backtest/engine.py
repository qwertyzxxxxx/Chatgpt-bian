from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from binance_ai_trader.domain.models import (
    BacktestMetrics,
    BacktestResult,
    BacktestSummary,
    SectorMember,
    StoredSignal,
)
from binance_ai_trader.evaluation import EvaluationPolicy, SignalEvaluationEngine
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.regime import MarketRegimeEngine
from binance_ai_trader.scoring import InsufficientDataError, ScoringEngine
from binance_ai_trader.sectors import SECTORS, SectorMap, SectorStrengthEngine
from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.signals.ranking import final_signal_score
from binance_ai_trader.space import SpaceEngine
from binance_ai_trader.signals import (
    RegimeSignalGate,
    SectorSignalGate,
    ShortSignalEngine,
    SignalCandidate,
    SignalEngine,
    SignalPolicy,
)


@dataclass(frozen=True, slots=True)
class BacktestPolicy:
    step_bars: int = 1
    maximum_evaluation_bars: int = 96

    def __post_init__(self) -> None:
        if self.step_bars < 1:
            raise ValueError("step_bars must be positive")
        if self.maximum_evaluation_bars < 1:
            raise ValueError("maximum_evaluation_bars must be positive")


class BacktestEngine:
    """Point-in-time replay of the current deterministic dual-direction strategy chain."""

    def __init__(
        self,
        repository: MarketDataRepository,
        sector_map: SectorMap,
        policy: BacktestPolicy | None = None,
        strategy_config: StrategyConfig | None = None,
    ) -> None:
        self._repository = repository
        self._sector_map = sector_map
        self._policy = policy or BacktestPolicy()
        self._regime = MarketRegimeEngine()
        self._sectors = SectorStrengthEngine()
        self._space = SpaceEngine()
        if strategy_config is None:
            self._scoring = ScoringEngine()
            self._regime_gate = RegimeSignalGate()
            self._sector_gate = SectorSignalGate()
            self._signals = SignalEngine()
            self._short_signals = ShortSignalEngine(self._signals.policy)
        else:
            self._scoring = ScoringEngine(strategy_config.scoring_weights)
            self._regime_gate = RegimeSignalGate(strategy_config.range_min_score)
            self._sector_gate = SectorSignalGate(
                strategy_config.sector_medium_min_score, strategy_config.sector_weak_min_score
            )
            self._signals = SignalEngine(
                SignalPolicy(
                    entry_distance_min_pct=Decimal(str(strategy_config.entry_distance_min_pct)),
                    entry_distance_max_pct=Decimal(str(strategy_config.entry_distance_max_pct)),
                    max_stop_loss_pct=Decimal(str(strategy_config.max_stop_loss_pct)),
                    min_rr_tp2=Decimal(str(strategy_config.min_rr_tp2)),
                )
            )
            self._short_signals = ShortSignalEngine(self._signals.policy)
        self._evaluation = SignalEvaluationEngine(
            EvaluationPolicy(self._policy.maximum_evaluation_bars)
        )

    def run(
        self, start_ms: int | None = None, end_ms: int | None = None,
        evaluation_times: Sequence[int] | None = None,
    ) -> BacktestSummary:
        started_at = _utc_now()
        run_id = f"backtest-{uuid4()}"
        self._repository.start_backtest_run(run_id, started_at, start_ms, end_ms, self._policy.step_bars)
        results: list[BacktestResult] = []
        available_points = (
            tuple(evaluation_times)
            if evaluation_times is not None
            else self._repository.load_backtest_evaluation_times(
                start_ms, end_ms, self._policy.maximum_evaluation_bars
            )
        )
        points = available_points[:: self._policy.step_bars]
        try:
            for evaluation_time_ms in points:
                results.extend(self._evaluate_point(run_id, evaluation_time_ms))
            completed_at = _utc_now()
            summary = summarize_results(run_id, started_at, completed_at, len(points), results)
            self._repository.save_backtest_results(run_id, results)
            self._repository.finish_backtest_run(run_id, completed_at, "SUCCEEDED", summary)
            return summary
        except Exception as error:
            self._repository.finish_backtest_run(run_id, _utc_now(), "FAILED", None, str(error))
            raise

    def _evaluate_point(
        self, backtest_run_id: str, evaluation_time_ms: int
    ) -> tuple[BacktestResult, ...]:
        snapshot_id = self._repository.create_backtest_snapshot(
            backtest_run_id, evaluation_time_ms, _utc_now()
        )
        universe = self._repository.load_backtest_universe(evaluation_time_ms)
        if not universe:
            return ()
        regime_data = {
            symbol: self._point_klines(symbol, evaluation_time_ms, self._regime.policy.minimum_candles)
            for symbol in ("BTCUSDT", "ETHUSDT")
        }
        regime = self._regime.evaluate(regime_data["BTCUSDT"], regime_data["ETHUSDT"])

        scored = []
        point_data = {}
        for symbol, tick_size in universe.items():
            klines = self._point_klines(symbol, evaluation_time_ms, 96)
            try:
                score = self._scoring.score(symbol, klines)
            except (InsufficientDataError, ValueError):
                continue
            scored.append((score, tick_size))
            point_data[symbol] = klines
        scored.sort(key=lambda item: (-item[0].score, item[0].symbol))
        if not scored:
            return ()

        members = tuple(self._sector_member(score, point_data[score.symbol]["15m"]) for score, _ in scored)
        snapshots = self._sectors.calculate(str(evaluation_time_ms), members, self._sector_map)
        sector_ranks = {item.sector: item.sector_rank for item in snapshots}
        candidates = [
            (score, tick_size, self._sector_map.sector_for(score.symbol))
            for score, tick_size in scored[:20]
        ]
        opportunities = []
        for score, tick_size, sector in candidates:
            sector_rank = sector_ranks.get(sector)
            weakness_score = round(100.0 - score.score, 2)
            for direction in self._regime_gate.allowed_directions(
                regime.combined_regime, score.score, weakness_score
            ):
                signal_score = score.score if direction == "LONG" else weakness_score
                if direction == "LONG" and not self._sector_gate.allows_long(
                    sector, sector_rank, signal_score, bool(sector_ranks)
                ):
                    continue
                capital_score = self._repository.load_capital_score_at(
                    score.symbol, evaluation_time_ms
                )
                history_4h = self._repository.load_klines_at(
                    score.symbol, "4h", evaluation_time_ms, self._space.REQUIRED_4H_BARS
                )
                try:
                    space_score = float(
                        self._space.score(
                            run_id_for_point(evaluation_time_ms), score.symbol, direction, history_4h
                        ).space_score
                    )
                except ValueError:
                    space_score = 50.0
                trend_score = _component_score(score.score_breakdown, "trend", score.score)
                final_score = final_signal_score(
                    capital_score=capital_score, space_score=space_score, trend_score=trend_score,
                    sector_rank=sector_rank, combined_regime=regime.combined_regime, direction=direction,
                )
                opportunities.append(
                    (score, tick_size, direction, signal_score, sector, sector_rank,
                     capital_score, space_score, final_score)
                )
        opportunities.sort(key=lambda item: (-item[8], item[2], item[0].symbol))

        signals = []
        direction_counts = {"LONG": 0, "SHORT": 0}
        for (score, tick_size, direction, signal_score, sector, sector_rank,
             capital_score, space_score, final_score) in opportunities:
            if direction_counts[direction] >= 3:
                continue
            directional_score = score if direction == "LONG" else type(score)(
                symbol=score.symbol,
                score=signal_score,
                score_breakdown={
                    **score.score_breakdown,
                    "weakness": {
                        "score": signal_score,
                        "source_strength_score": score.score,
                    },
                },
                algorithm_version=score.algorithm_version,
            )
            engine = self._signals if direction == "LONG" else self._short_signals
            try:
                signal = engine.generate(
                    SignalCandidate(
                        score=directional_score,
                        tick_size=tick_size,
                        klines=point_data[score.symbol],
                    )
                )
            except ValueError:
                signal = None
            if signal is None:
                continue
            signals.append((signal, sector, sector_rank, capital_score, space_score, final_score))
            direction_counts[direction] += 1

        completed = []
        generated_at = _epoch_iso(evaluation_time_ms)
        for signal, sector, sector_rank, capital_score, space_score, final_score in signals:
            future = self._repository.load_klines_after(
                signal.symbol, "15m", evaluation_time_ms, self._policy.maximum_evaluation_bars
            )
            evaluation = self._evaluation.evaluate(
                StoredSignal(
                    run_id=run_id_for_point(evaluation_time_ms),
                    symbol=signal.symbol,
                    direction=signal.direction,
                    entry=signal.entry,
                    stop_loss=signal.stop_loss,
                    tp1=signal.tp1,
                    tp2=signal.tp2,
                    generated_at=generated_at,
                    generated_at_ms=evaluation_time_ms,
                    snapshot_id=snapshot_id,
                ),
                future,
            )
            if evaluation is None:
                continue
            completed.append(
                BacktestResult(
                    evaluation_time_ms=evaluation_time_ms,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    combined_regime=regime.combined_regime,
                    sector=sector,
                    sector_rank=sector_rank,
                    score=signal.score,
                    entry=signal.entry,
                    stop_loss=signal.stop_loss,
                    tp1=signal.tp1,
                    tp2=signal.tp2,
                    rr_tp1=signal.rr_tp1,
                    rr_tp2=signal.rr_tp2,
                    result=evaluation.result,
                    bars_to_result=evaluation.bars_to_result,
                    realized_r=_realized_r(evaluation.result, signal.rr_tp1, signal.rr_tp2),
                    capital_score=capital_score,
                    space_score=space_score,
                    final_signal_score=final_score,
                    snapshot_id=snapshot_id,
                )
            )
        return tuple(completed)

    @staticmethod
    def _opportunity_key(item, snapshots: bool, combined_regime: str):
        score, _tick_size, direction, signal_score, _sector, sector_rank = item
        if not snapshots or combined_regime == "RANGE":
            return (-signal_score, 0 if direction == "LONG" else 1, score.symbol)
        sector_priority = (
            sector_rank if direction == "LONG" and sector_rank is not None
            else -sector_rank if direction == "SHORT" and sector_rank is not None
            else 10_000
        )
        return (sector_priority, -signal_score, 0 if direction == "LONG" else 1, score.symbol)

    def _point_klines(self, symbol: str, as_of_ms: int, limit: int):
        return {
            interval: self._repository.load_klines_at(symbol, interval, as_of_ms, limit)
            for interval in ("15m", "1h", "4h")
        }

    @staticmethod
    def _sector_member(score, fifteen_minute) -> SectorMember:
        window = fifteen_minute[-96:]
        change = Decimal("0")
        if len(window) >= 2 and window[0].open > 0:
            change = (window[-1].close - window[0].open) / window[0].open * Decimal("100")
        return SectorMember(
            symbol=score.symbol,
            score=score.score,
            change_24h=change,
            quote_volume_24h=sum((item.quote_volume for item in window), Decimal("0")),
        )


def summarize_results(
    run_id: str,
    started_at: str,
    completed_at: str,
    evaluation_points: int,
    results: Sequence[BacktestResult],
) -> BacktestSummary:
    return BacktestSummary(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        evaluation_points=evaluation_points,
        metrics=_metrics(results),
        by_direction=_group(results, lambda item: item.direction, ("LONG", "SHORT")),
        by_combined_regime=_group(
            results, lambda item: item.combined_regime, ("BULL", "BEAR", "RANGE", "OBSERVE")
        ),
        by_sector=_group(results, lambda item: item.sector, tuple(sorted(SECTORS))),
        by_score_bucket=_group(
            results, lambda item: _score_bucket(item.score),
            ("90-100", "80-90", "70-80", "below 70"),
        ),
        by_capital_bucket=_group(
            results, lambda item: _flow_bucket(item.capital_score),
            ("0-40", "40-60", "60-80", "80-100"),
        ),
        by_space_bucket=_group(
            results, lambda item: _flow_bucket(item.space_score),
            ("0-40", "40-60", "60-80", "80-100"),
        ),
    )


def _group(
    results: Sequence[BacktestResult], key, expected_groups: Sequence[str]
) -> dict[str, BacktestMetrics]:
    groups: dict[str, list[BacktestResult]] = {name: [] for name in expected_groups}
    for item in results:
        groups.setdefault(key(item), []).append(item)
    return {name: _metrics(items) for name, items in groups.items()}


def _metrics(results: Sequence[BacktestResult]) -> BacktestMetrics:
    total = len(results)
    tp2 = sum(item.result == "WIN_TP2" for item in results)
    tp1 = sum(item.result in {"TP1_HIT", "WIN_TP2"} for item in results)
    losses = sum(item.result == "LOSS" for item in results)
    expired = sum(item.result == "EXPIRED" for item in results)
    gains = sum((item.realized_r for item in results if item.realized_r > 0), Decimal("0"))
    gross_loss = -sum((item.realized_r for item in results if item.realized_r < 0), Decimal("0"))
    profit_factor = None if gross_loss == 0 else round(float(gains / gross_loss), 4)
    expectancy = sum((item.realized_r for item in results), Decimal("0")) / Decimal(total) if total else Decimal("0")
    average_rr = sum((item.rr_tp2 for item in results), Decimal("0")) / Decimal(total) if total else Decimal("0")
    return BacktestMetrics(
        total_signals=total,
        tp1_hit_rate=_rate(tp1, total),
        tp2_win_rate=_rate(tp2, total),
        loss_rate=_rate(losses, total),
        expired_rate=_rate(expired, total),
        profit_factor=profit_factor,
        expectancy_r=round(float(expectancy), 4),
        max_drawdown_r=round(float(_max_drawdown(results)), 4),
        avg_rr_tp2=round(float(average_rr), 4),
    )


def _max_drawdown(results: Sequence[BacktestResult]) -> Decimal:
    equity = peak = Decimal("0")
    maximum = Decimal("0")
    for item in sorted(results, key=lambda row: (row.evaluation_time_ms, row.symbol)):
        equity += item.realized_r
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _realized_r(result: str, rr_tp1: Decimal, rr_tp2: Decimal) -> Decimal:
    if result == "LOSS":
        return Decimal("-1")
    if result == "WIN_TP2":
        return rr_tp2
    if result == "TP1_HIT":
        return rr_tp1
    return Decimal("0")


def _component_score(breakdown: dict[str, object], name: str, fallback: float) -> float:
    value = breakdown.get(name)
    if isinstance(value, dict) and "score" in value:
        return float(value["score"])
    if isinstance(value, (int, float)):
        return float(value)
    return fallback



def _score_bucket(score: float) -> str:
    if score >= 90:
        return "90-100"
    if score >= 80:
        return "80-90"
    if score >= 70:
        return "70-80"
    return "below 70"


def _flow_bucket(score: float) -> str:
    if score < 40:
        return "0-40"
    if score < 60:
        return "40-60"
    if score < 80:
        return "60-80"
    return "80-100"


def _rate(count: int, total: int) -> float:
    return round(count / total * 100, 2) if total else 0.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _epoch_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat(timespec="milliseconds")


def run_id_for_point(value: int) -> str:
    return f"backtest-point-{value}"
