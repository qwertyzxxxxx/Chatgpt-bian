"""Tests for StrategyConfig and load_all_strategy_configs."""
from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.config import StrategyConfig, load_all_strategy_configs


def _write_json(directory: Path, name: str, data: dict) -> Path:
    p = directory / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


_BASELINE = {
    "strategy_id": "baseline_v1",
    "name": "Baseline V1",
    "description": "Standard baseline",
    "entry_distance_min_pct": -3.0,
    "entry_distance_max_pct": 1.0,
    "max_stop_loss_pct": 7.0,
    "min_rr_tp2": 2.0,
}

_BREAKOUT = {
    "strategy_id": "breakout_hunter_v1",
    "name": "Breakout Hunter V1",
    "entry_distance_min_pct": -3.0,
    "entry_distance_max_pct": 1.0,
    "max_stop_loss_pct": 5.0,
    "min_rr_tp2": 2.0,
    "enabled_regimes": ["BULL", "BEAR", "RANGE"],
    "enabled_directions": ["LONG", "SHORT"],
    "capital_score_min": 60.0,
    "space_score_min": 80.0,
    "output_limit": 3,
}

_BEAR_SHORT = {
    "strategy_id": "bear_short_space80_v1",
    "enabled_regimes": ["BEAR"],
    "enabled_directions": ["SHORT"],
    "space_score_min": 80.0,
}

_RANGE_DISABLED = {
    "strategy_id": "range_disabled_v1",
    "enabled_regimes": ["BULL", "BEAR"],
    "enabled_directions": ["LONG", "SHORT"],
}


class StrategyConfigFromFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_baseline_loads_with_defaults(self):
        p = _write_json(self._dir, "baseline_v1.json", _BASELINE)
        cfg = StrategyConfig.from_file(p)
        self.assertEqual(cfg.strategy_id, "baseline_v1")
        self.assertEqual(cfg.max_stop_loss_pct, 7.0)
        self.assertEqual(cfg.min_rr_tp2, 2.0)
        self.assertEqual(cfg.capital_score_min, 0.0)
        self.assertEqual(cfg.capital_score_max, 100.0)
        self.assertEqual(cfg.space_score_min, 0.0)
        self.assertIsNone(cfg.output_limit)
        self.assertIn("BULL", cfg.enabled_regimes)
        self.assertIn("BEAR", cfg.enabled_regimes)
        self.assertIn("RANGE", cfg.enabled_regimes)
        self.assertIn("LONG", cfg.enabled_directions)
        self.assertIn("SHORT", cfg.enabled_directions)

    def test_breakout_hunter_custom_fields(self):
        p = _write_json(self._dir, "breakout_hunter_v1.json", _BREAKOUT)
        cfg = StrategyConfig.from_file(p)
        self.assertEqual(cfg.strategy_id, "breakout_hunter_v1")
        self.assertEqual(cfg.max_stop_loss_pct, 5.0)
        self.assertEqual(cfg.capital_score_min, 60.0)
        self.assertEqual(cfg.space_score_min, 80.0)
        self.assertEqual(cfg.output_limit, 3)

    def test_bear_short_enabled_regimes(self):
        p = _write_json(self._dir, "bear_short.json", _BEAR_SHORT)
        cfg = StrategyConfig.from_file(p)
        self.assertEqual(cfg.enabled_regimes, frozenset({"BEAR"}))
        self.assertEqual(cfg.enabled_directions, frozenset({"SHORT"}))

    def test_range_disabled_excludes_range(self):
        p = _write_json(self._dir, "range_disabled.json", _RANGE_DISABLED)
        cfg = StrategyConfig.from_file(p)
        self.assertNotIn("RANGE", cfg.enabled_regimes)
        self.assertIn("BULL", cfg.enabled_regimes)
        self.assertIn("BEAR", cfg.enabled_regimes)

    def test_to_signal_policy_uses_config_values(self):
        p = _write_json(self._dir, "breakout.json", _BREAKOUT)
        cfg = StrategyConfig.from_file(p)
        policy = cfg.to_signal_policy()
        self.assertEqual(policy.max_stop_loss_pct, Decimal("5.0"))
        self.assertEqual(policy.min_rr_tp2, Decimal("2.0"))


class LoadAllStrategyConfigsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_empty_list_for_empty_dir(self):
        configs = load_all_strategy_configs(self._dir)
        self.assertEqual(configs, [])

    def test_loads_multiple_configs(self):
        _write_json(self._dir, "baseline_v1.json", _BASELINE)
        _write_json(self._dir, "breakout_hunter_v1.json", _BREAKOUT)
        configs = load_all_strategy_configs(self._dir)
        self.assertEqual(len(configs), 2)

    def test_sorted_by_filename(self):
        _write_json(self._dir, "zzz_strategy.json", {**_BASELINE, "strategy_id": "zzz_strategy"})
        _write_json(self._dir, "aaa_strategy.json", {**_BASELINE, "strategy_id": "aaa_strategy"})
        configs = load_all_strategy_configs(self._dir)
        ids = [c.strategy_id for c in configs]
        self.assertEqual(ids, sorted(ids))

    def test_skips_malformed_json(self):
        (self._dir / "bad.json").write_text("{broken json", encoding="utf-8")
        _write_json(self._dir, "good.json", _BASELINE)
        configs = load_all_strategy_configs(self._dir)
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].strategy_id, "baseline_v1")

    def test_skips_missing_strategy_id(self):
        (self._dir / "no_id.json").write_text(json.dumps({"name": "oops"}), encoding="utf-8")
        configs = load_all_strategy_configs(self._dir)
        self.assertEqual(len(configs), 0)

    def test_real_strategy_dir_loads_five(self):
        real_dir = Path(__file__).parent.parent.parent / "config" / "strategies"
        if not real_dir.exists():
            self.skipTest("config/strategies not found")
        configs = load_all_strategy_configs(real_dir)
        ids = {c.strategy_id for c in configs}
        self.assertIn("baseline_v1", ids)
        self.assertGreaterEqual(len(ids), 5)


class StrategyConfigFrozenTest(unittest.TestCase):
    def test_is_immutable(self):
        cfg = StrategyConfig(strategy_id="test_v1")
        with self.assertRaises((AttributeError, TypeError)):
            cfg.strategy_id = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
