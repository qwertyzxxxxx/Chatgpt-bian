from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from binance_ai_trader.domain.models import BacktestMetrics
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.scoring import ScoringEngine
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.signals import RegimeSignalGate, SectorSignalGate, SignalEngine, SignalPolicy
from binance_ai_trader.strategy_lab.config import StrategyConfig
from binance_ai_trader.strategy_lab.service import StrategyLab
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
            def __init__(
                self, _repository, _sector_map, _policy, strategy_config, result_filter
            ) -> None:
                self.strategy_id = strategy_config.strategy_id
                self.result_filter = result_filter

            def run(self, _start, _end, evaluation_times):
                captured.append((self.strategy_id, tuple(evaluation_times)))
                return SimpleNamespace(metrics=metrics, by_combined_regime={})

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
