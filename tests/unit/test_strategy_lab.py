from decimal import Decimal
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from binance_ai_trader.domain.models import BacktestMetrics, BacktestResult
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.scoring import ScoringEngine
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.signals import RegimeSignalGate, SectorSignalGate, SignalEngine, SignalPolicy
from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.strategy_lab.models import ChampionStanding, StrategyComparison
from binance_ai_trader.strategy_lab.models import StrategySweepResult
from binance_ai_trader.strategy_lab.reporting import (
    render_champion_league_markdown,
    render_sweep_markdown,
)
from binance_ai_trader.strategy_lab.service import (
    StrategyLab,
    _research_results,
    _champion_scores,
    _sweep_rank,
    _verdict,
)
from tests.unit.test_scoring_engine import market
from tests.unit.test_signal_engine import candidate


BASELINE = Path("config/strategies/baseline_v1.json")


class StrategyLabTest(unittest.TestCase):
    def test_baseline_config_preserves_current_engine_behavior(self) -> None:
        config = StrategyConfig.load(BASELINE)
        default_score = ScoringEngine().score("BTCUSDT", market("BTCUSDT", 0.005))
        configured_score = ScoringEngine(config.scoring_weights).score(
            "BTCUSDT", market("BTCUSDT", 0.005)
        )
        default_signal = SignalEngine().generate(candidate())
        configured_signal = SignalEngine(
            SignalPolicy(
                Decimal(str(config.entry_distance_min_pct)),
                Decimal(str(config.entry_distance_max_pct)),
                Decimal(str(config.max_stop_loss_pct)),
                Decimal(str(config.min_rr_tp2)),
            )
        ).generate(candidate())

        self.assertEqual(default_score, configured_score)
        self.assertEqual(default_signal, configured_signal)
        self.assertEqual(
            RegimeSignalGate().allows_long("RANGE", 80),
            RegimeSignalGate(config.range_min_score).allows_long("RANGE", 80),
        )
        self.assertEqual(
            SectorSignalGate().allows_long("DEFI", 4, 85, True),
            SectorSignalGate(
                config.sector_medium_min_score, config.sector_weak_min_score
            ).allows_long("DEFI", 4, 85, True),
        )
        self.assertEqual(96, config.evaluation_window_bars)
        self.assertNotIn("enabled_regimes", config.as_dict())
        self.assertNotIn("space_score_min", config.as_dict())

    def test_compare_passes_identical_evaluation_times_to_every_strategy(self) -> None:
        metrics = BacktestMetrics(0, 0, 0, 0, 0, None, 0, 0, 0)
        captured: list[tuple[str, tuple[int, ...]]] = []

        class FakeBacktestEngine:
            def __init__(self, _repository, _sector_map, _policy, strategy_config) -> None:
                self.strategy_id = strategy_config.strategy_id

            def run(self, _start, _end, evaluation_times):
                captured.append((self.strategy_id, tuple(evaluation_times)))
                return SimpleNamespace(
                    run_id=f"run-{self.strategy_id}",
                    started_at="start",
                    completed_at="complete",
                    evaluation_points=len(evaluation_times),
                )

        with tempfile.TemporaryDirectory() as directory:
            repository = MarketDataRepository(Path(directory) / "lab.db")
            try:
                lab = StrategyLab(repository, SectorMap({}), BASELINE)
                baseline = lab.ensure_baseline().config
                candidate_config = baseline.candidate(
                    "candidate_manual", "Manual candidate", "Test candidate",
                    range_min_score=82,
                )
                repository.register_strategy_version(candidate_config, "candidate", "now")
                with (
                    patch.object(repository, "load_backtest_evaluation_times", return_value=(10, 20, 30)),
                    patch("binance_ai_trader.strategy_lab.service.BacktestEngine", FakeBacktestEngine),
                    patch(
                        "binance_ai_trader.strategy_lab.service._comparison_from_results",
                        side_effect=lambda strategy_id, *_args: StrategyComparison(
                            strategy_id, metrics, {}, {}
                        ),
                    ),
                ):
                    comparisons = lab.compare(("baseline_v1", "candidate_manual"))
            finally:
                repository.close()

        self.assertEqual(("baseline_v1", "candidate_manual"), tuple(item.strategy_id for item in comparisons))
        self.assertEqual(
            [("baseline_v1", (10, 20, 30)), ("candidate_manual", (10, 20, 30))],
            captured,
        )

    def test_configured_phase_one_variants_apply_research_filters(self) -> None:
        result = SimpleNamespace(
            combined_regime="BEAR", direction="SHORT", capital_score=70, space_score=85
        )
        expected = {
            "range_disabled_v1": True,
            "bear_short_space80_v1": True,
            "capital_60_80_space80_v1": True,
        }
        for strategy_id, included in expected.items():
            config = StrategyConfig.load(Path(f"config/strategies/{strategy_id}.json"))
            self.assertEqual(included, config.includes_result(result))

        range_config = StrategyConfig.load(Path("config/strategies/range_disabled_v1.json"))
        self.assertFalse(
            range_config.includes_result(
                SimpleNamespace(
                    combined_regime="RANGE", direction="LONG", capital_score=70, space_score=85
                )
            )
        )
        bear_config = StrategyConfig.load(Path("config/strategies/bear_short_space80_v1.json"))
        self.assertFalse(
            bear_config.includes_result(
                SimpleNamespace(
                    combined_regime="BEAR", direction="SHORT", capital_score=70, space_score=79
                )
            )
        )
        capital_config = StrategyConfig.load(
            Path("config/strategies/capital_60_80_space80_v1.json")
        )
        self.assertFalse(
            capital_config.includes_result(
                SimpleNamespace(
                    combined_regime="BULL", direction="LONG", capital_score=81, space_score=85
                )
            )
        )

    def test_phase_two_verdict_rules(self) -> None:
        def comparison(
            trades: int, expectancy: float, profit_factor: float | None, drawdown: float
        ) -> StrategyComparison:
            return StrategyComparison(
                "candidate",
                BacktestMetrics(
                    trades, 0, 0, 0, 0, profit_factor, expectancy, drawdown, 0
                ),
                {},
                {},
            )

        self.assertEqual("PASS", _verdict(comparison(20, 0.3, 1.2, 10)))
        self.assertEqual("WATCH", _verdict(comparison(19, 0.3, 1.2, 2)))
        self.assertEqual("REJECT", _verdict(comparison(20, 0, 1.2, 2)))
        self.assertEqual("REJECT", _verdict(comparison(20, 0.3, 0.9, 2)))

    def test_sweep_ranking_prioritizes_expectancy_pf_drawdown_then_trades(self) -> None:
        def item(
            name: str, trades: int, expectancy: float, pf: float, drawdown: float
        ) -> tuple[dict[str, float], StrategyComparison]:
            return (
                {"abs_move_percentile": float(name)},
                StrategyComparison(
                    "breakout_hunter_v1",
                    BacktestMetrics(
                        trades, 0, 0, 0, 0, pf, expectancy, drawdown, 0
                    ),
                    {},
                    {},
                ),
            )

        ranked = sorted(
            (
                item("5", 100, 0.2, 2.0, 3),
                item("4", 20, 0.3, 1.4, 4),
                item("3", 20, 0.3, 1.5, 5),
                item("2", 20, 0.3, 1.5, 4),
                item("1", 30, 0.3, 1.5, 4),
            ),
            key=_sweep_rank,
        )

        self.assertEqual(
            [1.0, 2.0, 3.0, 4.0, 5.0],
            [entry[0]["abs_move_percentile"] for entry in ranked],
        )

    def test_sweep_markdown_contains_metadata_top_ten_and_best_parameters(self) -> None:
        result = StrategySweepResult(
            rank=1,
            parameters={
                "abs_move_percentile": 0.9,
                "quote_volume_min": 10_000_000.0,
            },
            metrics=BacktestMetrics(25, 60, 52, 20, 0, 1.5, 0.4, 3.0, 2.5),
            verdict="PASS",
        )

        report = render_sweep_markdown(
            "breakout_hunter_v1",
            Path("data/market_data.db"),
            (result,),
            864,
            datetime(2026, 6, 14, 12, 30, tzinfo=UTC),
        )

        self.assertIn("2026-06-14T12:30:00+00:00", report)
        self.assertIn("`data/market_data.db`", report)
        self.assertIn("Total parameter combinations tested:** 864", report)
        self.assertIn("## Top 10", report)
        self.assertIn("**PASS**", report)
        self.assertIn("## Best Parameter Set", report)
        self.assertIn("abs_move_percentile:** 0.9", report)
        self.assertIn("research only", report.lower())
        self.assertIn("not live trading advice", report.lower())

    def test_champion_scores_apply_weighted_normalized_formula(self) -> None:
        weaker = StrategyComparison(
            "weaker",
            BacktestMetrics(10, 0, 0, 0, 0, 1.0, 0.1, 5.0, 0),
            {},
            {},
        )
        stronger = StrategyComparison(
            "stronger",
            BacktestMetrics(20, 0, 0, 0, 0, 2.0, 0.5, 1.0, 0),
            {},
            {},
        )

        scores = _champion_scores((weaker, stronger))

        self.assertEqual(0.0, scores["weaker"])
        self.assertAlmostEqual(1.0, scores["stronger"])

    def test_champion_report_contains_champion_and_leaderboard(self) -> None:
        standing = ChampionStanding(
            rank=1,
            strategy_id="breakout_hunter_v1",
            score=0.9,
            metrics=BacktestMetrics(25, 0, 52, 0, 0, 1.5, 0.4, 3.0, 0),
            verdict="PASS",
        )

        report = render_champion_league_markdown(
            Path("data/market_data.db"),
            (standing,),
            datetime(2026, 6, 14, 12, 30, tzinfo=UTC),
        )

        self.assertIn("2026-W24", report)
        self.assertIn("## Champion", report)
        self.assertIn("`breakout_hunter_v1`", report)
        self.assertIn("## Leaderboard", report)
        self.assertIn("**PASS**", report)
        self.assertIn("not live trading advice", report)

    def test_breakout_hunter_selects_qualified_top_twenty_percent_and_top_three(self) -> None:
        config = StrategyConfig.load(
            Path("config/strategies/breakout_hunter_v1.json")
        )
        results = tuple(
            BacktestResult(
                evaluation_time_ms=1,
                symbol=f"S{index}USDT",
                direction="LONG" if index % 2 else "SHORT",
                combined_regime="BULL" if index % 2 else "BEAR",
                sector="DEFI",
                sector_rank=1,
                score=90,
                entry=Decimal("100"),
                stop_loss=Decimal("95"),
                tp1=Decimal("105"),
                tp2=Decimal("110"),
                rr_tp1=Decimal("1"),
                rr_tp2=Decimal("2"),
                result="WIN_TP2",
                bars_to_result=1,
                realized_r=Decimal("2"),
                capital_score=60,
                space_score=80,
                change_24h=Decimal(str(index)),
                quote_volume_24h=Decimal("5000000"),
            )
            for index in range(1, 21)
        )

        selected = _research_results(config, results)

        self.assertEqual(["S20USDT", "S19USDT", "S18USDT"], [item.symbol for item in selected])
        self.assertEqual({"LONG", "SHORT"}, {item.direction for item in selected})

    def test_breakout_hunter_rejects_weak_flow_space_rr_stop_and_volume(self) -> None:
        config = StrategyConfig.load(
            Path("config/strategies/breakout_hunter_v1.json")
        )
        base = SimpleNamespace(
            combined_regime="BULL",
            direction="LONG",
            capital_score=60,
            space_score=80,
            rr_tp2=Decimal("2"),
            entry=Decimal("100"),
            stop_loss=Decimal("95"),
        )
        self.assertTrue(config.includes_result(base))
        for changes in (
            {"capital_score": 59},
            {"space_score": 79},
            {"rr_tp2": Decimal("1.99")},
            {"stop_loss": Decimal("94.99")},
        ):
            self.assertFalse(config.includes_result(SimpleNamespace(**(vars(base) | changes))))

    def test_candidate_cannot_be_loaded_for_manual_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = MarketDataRepository(Path(directory) / "lab.db")
            try:
                lab = StrategyLab(repository, SectorMap({}), BASELINE)
                baseline = lab.ensure_baseline().config
                candidate_config = baseline.candidate(
                    "candidate_blocked", "Blocked", "Must remain research-only"
                )
                repository.register_strategy_version(candidate_config, "candidate", "now")
                with self.assertRaisesRegex(ValueError, "approved"):
                    lab.load_for_manual_run("candidate_blocked")
                self.assertEqual("baseline_v1", lab.load_for_manual_run("baseline_v1").strategy_id)
            finally:
                repository.close()

    def test_candidate_generation_does_not_mutate_baseline(self) -> None:
        baseline = StrategyConfig.load(BASELINE)
        original = baseline.as_dict()
        candidates = StrategyLab._candidate_configs(baseline)
        self.assertEqual(original, baseline.as_dict())
        self.assertEqual(20, len(candidates))
        self.assertTrue(all(item.strategy_id != "baseline_v1" for item in candidates))


if __name__ == "__main__":
    unittest.main()
