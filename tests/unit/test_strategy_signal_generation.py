"""Tests for strategy-aware SignalGenerator (regime/direction/capital/space filters)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from binance_ai_trader.config import StrategyConfig
from binance_ai_trader.domain.models import SignalResult
from binance_ai_trader.application.generate_signals import SignalGenerator


def _bear_short_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="bear_short_space80_v1",
        enabled_regimes=frozenset({"BEAR"}),
        enabled_directions=frozenset({"SHORT"}),
        space_score_min=80.0,
    )


def _range_disabled_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="range_disabled_v1",
        enabled_regimes=frozenset({"BULL", "BEAR"}),
        enabled_directions=frozenset({"LONG", "SHORT"}),
    )


def _capital_60_80_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_id="capital_60_80_space80_v1",
        enabled_regimes=frozenset({"BULL", "BEAR", "RANGE"}),
        enabled_directions=frozenset({"LONG", "SHORT"}),
        capital_score_min=60.0,
        capital_score_max=80.0,
        space_score_min=80.0,
    )


def _make_mock_repo(combined_regime: str = "RANGE") -> MagicMock:
    """Build a minimal mock repository that returns empty scores for the snapshot."""
    repo = MagicMock()
    mock_snapshot = MagicMock()
    mock_snapshot.snapshot_id = "snap-test"
    mock_snapshot.collection_run_id = "run-test"
    mock_snapshot.data_cutoff_ms = 0
    repo.load_snapshot.return_value = mock_snapshot
    repo.load_scores_for_snapshot.return_value = ()
    repo.load_combined_regime.return_value = combined_regime
    return repo


class RegimeFilterTest(unittest.TestCase):
    def _generate_with_config(self, strategy_config: StrategyConfig, regime: str) -> SignalResult:
        repo = _make_mock_repo(regime)
        gen = SignalGenerator(repo, strategy_config=strategy_config)
        return gen.generate_latest(snapshot_id="snap-test")

    def test_bear_short_skips_when_regime_is_bull(self):
        result = self._generate_with_config(_bear_short_config(), "BULL")
        self.assertEqual(len(result.signals), 0)

    def test_bear_short_skips_when_regime_is_range(self):
        result = self._generate_with_config(_bear_short_config(), "RANGE")
        self.assertEqual(len(result.signals), 0)

    def test_range_disabled_skips_when_regime_is_range(self):
        result = self._generate_with_config(_range_disabled_config(), "RANGE")
        self.assertEqual(len(result.signals), 0)

    def test_range_disabled_allows_bull(self):
        result = self._generate_with_config(_range_disabled_config(), "BULL")
        self.assertEqual(len(result.signals), 0)

    def test_baseline_allows_all_regimes(self):
        for regime in ("BULL", "BEAR", "RANGE"):
            result = self._generate_with_config(
                StrategyConfig(strategy_id="baseline_v1"), regime
            )
            self.assertEqual(len(result.signals), 0)

    def test_save_signals_called_on_regime_skip(self):
        repo = _make_mock_repo("BULL")
        repo.load_scores_for_snapshot.return_value = (MagicMock(),)
        repo.load_scores_for_snapshot.return_value[0].run_id = "run-test"
        repo.load_combined_regime.return_value = "BULL"
        gen = SignalGenerator(repo, strategy_config=_bear_short_config())
        gen.generate_latest(snapshot_id="snap-test")
        repo.save_signals.assert_called_once()
        repo.finalize_snapshot.assert_called_once()


class StrategyConfigSignalPolicyTest(unittest.TestCase):
    def test_breakout_hunter_uses_tighter_stop(self):
        from decimal import Decimal
        cfg = StrategyConfig(
            strategy_id="breakout_hunter_v1",
            max_stop_loss_pct=5.0,
        )
        policy = cfg.to_signal_policy()
        self.assertEqual(policy.max_stop_loss_pct, Decimal("5.0"))

    def test_baseline_uses_default_stop(self):
        from decimal import Decimal
        cfg = StrategyConfig(strategy_id="baseline_v1")
        policy = cfg.to_signal_policy()
        self.assertEqual(policy.max_stop_loss_pct, Decimal("7.0"))


class ForkSnapshotTest(unittest.TestCase):
    def setUp(self):
        from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "test.db"
        repo = MarketDataRepository(self._db_path)
        repo.close()

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_run(self, repo, run_id: str) -> None:
        """Insert a minimal collection_run + baseline scan snapshot so FK constraints pass."""
        repo.start_run(run_id, "2024-01-01T00:00:00.000+00:00")

    def test_fork_creates_new_snapshot_with_strategy_id(self):
        from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
        repo = MarketDataRepository(self._db_path)
        run_id = "run-abc"
        self._seed_run(repo, run_id)
        snapshot_id = repo.fork_snapshot_for_strategy(
            run_id=run_id,
            strategy_id="breakout_hunter_v1",
            data_cutoff_ms=1000000,
            created_at="2024-01-01T00:00:00",
        )
        repo.close()
        con = sqlite3.connect(str(self._db_path))
        row = con.execute(
            "SELECT snapshot_id, strategy_id, collection_run_id FROM analysis_snapshots WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        con.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "breakout_hunter_v1")
        self.assertEqual(row[2], run_id)

    def test_fork_snapshot_id_includes_strategy(self):
        from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
        repo = MarketDataRepository(self._db_path)
        run_id = "run-xyz"
        self._seed_run(repo, run_id)
        sid = repo.fork_snapshot_for_strategy(
            run_id=run_id,
            strategy_id="bear_short_space80_v1",
            data_cutoff_ms=0,
            created_at="2024-01-01T00:00:00",
        )
        repo.close()
        self.assertIn("bear_short_space80_v1", sid)

    def test_fork_is_idempotent(self):
        from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
        repo = MarketDataRepository(self._db_path)
        run_id = "run-idem"
        self._seed_run(repo, run_id)
        sid1 = repo.fork_snapshot_for_strategy(run_id, "strategy_x", 0, "2024-01-01T00:00:00")
        sid2 = repo.fork_snapshot_for_strategy(run_id, "strategy_x", 0, "2024-01-01T00:00:00")
        repo.close()
        self.assertEqual(sid1, sid2)


if __name__ == "__main__":
    unittest.main()
