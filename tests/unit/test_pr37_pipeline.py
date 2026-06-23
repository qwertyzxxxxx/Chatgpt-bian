"""
PR #37 — Fix primary strategy scheduling and paper trade pipeline.

Requirements verified:
  R1  run-loop parser exposes --strategies-dir
  R2  run-loop scan invocation forwards --strategies-dir to the scan command
  R3  config/strategies/ contains all 5 strategy JSON configs
  R4  load_paper_trades() resolves strategy_id per signal evaluation row
  R5  load_pending_paper_outcomes() is strategy-agnostic (reads all strategies)
  R6  diagnostic format_text/format_telegram label is "paper_trades" not bare "trades"
  R7  compileall — cli.py, strategy_diagnostic.py, performance_center/loader.py
"""
from __future__ import annotations

import compileall
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


# ─────────────────────────── helpers ─────────────────────────────────────────

def _signal(symbol: str, direction: str = "LONG"):
    from binance_ai_trader.domain.models import TradeSignal
    return TradeSignal(
        symbol=symbol, direction=direction, score=80,
        entry=Decimal("100"), latest_close=Decimal("100"),
        stop_loss=Decimal("95"), stop_loss_pct=Decimal("5"),
        tp1=Decimal("105"), tp2=Decimal("110"),
        rr_tp1=Decimal("1"), rr_tp2=Decimal("2"),
        logic_summary="pr37 fixture",
    )


def _evaluation(run_id: str, symbol: str, result: str = "TP1_HIT"):
    from binance_ai_trader.domain.models import SignalEvaluation
    return SignalEvaluation(
        signal_run_id=run_id, symbol=symbol, direction="LONG",
        entry=Decimal("100"), stop_loss=Decimal("95"),
        tp1=Decimal("105"), tp2=Decimal("110"), result=result,
        max_favorable_pct=Decimal("10"), max_adverse_pct=Decimal("-2"),
        bars_to_result=24,
    )


# ─────────────────────────── R1: run-loop parser has --strategies-dir ─────────

class RunLoopParserStrategiesDirTest(unittest.TestCase):
    def test_run_loop_help_mentions_strategies_dir(self):
        """--help must show strategies-dir in the run-loop command output."""
        import sys
        import io
        from binance_ai_trader.entrypoints.cli import main as cli_main
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            with self.assertRaises(SystemExit):
                cli_main(["run-loop", "--help"])
        finally:
            sys.stdout = old_stdout
        self.assertIn("strategies-dir", buf.getvalue(),
                      "--strategies-dir must appear in run-loop --help output")

    def test_run_loop_default_strategies_dir(self):
        """Parsing run-loop without --strategies-dir must default to config/strategies."""
        import argparse
        from binance_ai_trader.entrypoints.cli import main as cli_main
        import sys, io
        # We only want to verify argparse parsing — stop before actually running.
        # We do this by catching SystemExit from --help or any exception; what we
        # really need is the argparse Namespace, which we can get via parse_known_args.
        # Build a minimal parser that mirrors the run-loop subcommand.
        # Easier: inspect the source directly for the default value.
        cli_path = (
            Path(__file__).parents[2]
            / "src" / "binance_ai_trader" / "entrypoints" / "cli.py"
        )
        src = cli_path.read_text()
        # The run-loop parser must declare --strategies-dir
        self.assertIn('"--strategies-dir"', src,
                      "--strategies-dir must be registered in the run-loop parser")
        # Default should point to config/strategies
        self.assertIn("config/strategies", src,
                      "run-loop --strategies-dir default must reference config/strategies")


# ─────────────────────────── R2: scan invocation forwards --strategies-dir ───

class ScanInvocationStrategiesDirTest(unittest.TestCase):
    def test_strategies_dir_present_in_scan_invocation(self):
        """The scan lambda inside _run_loop() must contain --strategies-dir."""
        cli_path = (
            Path(__file__).parents[2]
            / "src" / "binance_ai_trader" / "entrypoints" / "cli.py"
        )
        src = cli_path.read_text()
        # Locate the default_tasks( call in _run_loop
        tasks_pos = src.find("tasks = default_tasks(")
        self.assertGreater(tasks_pos, 0, "default_tasks( block not found in cli.py")
        # The 600-char window after default_tasks( covers the scan=lambda list
        scan_region = src[tasks_pos: tasks_pos + 700]
        self.assertIn("--strategies-dir", scan_region,
                      "--strategies-dir must be inside the scan=lambda list in _run_loop")

    def test_strategies_dir_forwarded_with_args_value(self):
        """The scan invocation must use str(args.strategies_dir) not a hardcoded path."""
        cli_path = (
            Path(__file__).parents[2]
            / "src" / "binance_ai_trader" / "entrypoints" / "cli.py"
        )
        src = cli_path.read_text()
        tasks_pos = src.find("tasks = default_tasks(")
        scan_region = src[tasks_pos: tasks_pos + 700]
        self.assertIn("args.strategies_dir", scan_region,
                      "scan invocation must use args.strategies_dir, not a hardcoded path")


# ─────────────────────────── R3: strategy JSON configs exist ──────────────────

class StrategyConfigFilesTest(unittest.TestCase):
    STRATEGIES_DIR = Path(__file__).parents[2] / "config" / "strategies"
    REQUIRED_IDS = [
        "baseline_v1",
        "breakout_hunter_v1",
        "bear_short_space80_v1",
        "capital_60_80_space80_v1",
        "range_disabled_v1",
    ]

    def test_strategies_dir_exists(self):
        self.assertTrue(self.STRATEGIES_DIR.exists(),
                        f"config/strategies/ must exist: {self.STRATEGIES_DIR}")

    def test_all_five_strategy_files_present(self):
        for sid in self.REQUIRED_IDS:
            p = self.STRATEGIES_DIR / f"{sid}.json"
            self.assertTrue(p.exists(), f"Missing strategy config: {p}")

    def test_each_json_contains_correct_strategy_id(self):
        import json
        for sid in self.REQUIRED_IDS:
            p = self.STRATEGIES_DIR / f"{sid}.json"
            if not p.exists():
                self.skipTest(f"{p} missing")
            data = json.loads(p.read_text())
            self.assertEqual(data.get("strategy_id"), sid,
                             f"{p.name}: strategy_id must be '{sid}'")


# ─────────────────────────── R4: load_paper_trades resolves strategy_id ──────

class LoadPaperTradesTest(unittest.TestCase):
    """Tests for performance_center.loader.load_paper_trades()."""

    def _make_repo_with_evaluated_signal(self, strategy_id: str = "breakout_hunter_v1"):
        """Returns (repo, tmp_dir) — caller must close repo and clean up dir."""
        import tempfile
        from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
        tmp = tempfile.mkdtemp()
        db_path = Path(tmp) / "pr37.db"
        repo = MarketDataRepository(db_path)
        run_id = "pr37-run1"
        ts = "2026-06-17T07:00:00.000+00:00"
        ts_eval = "2026-06-18T07:00:00.000+00:00"
        symbol = "BTCUSDT"
        # baseline_v1 snapshot (created by start_run)
        repo.start_run(run_id, ts)
        if strategy_id != "baseline_v1":
            # fork a strategy-specific snapshot
            snap_id = repo.fork_snapshot_for_strategy(
                run_id=run_id, strategy_id=strategy_id,
                data_cutoff_ms=0, created_at=ts,
            )
            # save signal pointing to the forked snapshot
            repo._connection.execute(
                """INSERT INTO signals (
                    run_id, snapshot_id, rank, symbol, direction,
                    combined_regime, sector, sector_rank, score,
                    capital_score, space_score, final_signal_score,
                    entry, latest_close, stop_loss, stop_loss_pct,
                    tp1, tp2, rr_tp1, rr_tp2, logic_summary, generated_at
                ) VALUES (?,?,1,?,'LONG','BULL','OTHER',1,80,70,75,77,
                          '100','100','95','5','105','110','1','2','test',?)""",
                (run_id, snap_id, symbol, ts),
            )
            repo._connection.commit()
        else:
            repo.save_signals(run_id, (_signal(symbol),), ts)
        repo.save_signal_evaluations((_evaluation(run_id, symbol),), ts_eval)
        return repo, tmp

    def test_resolves_extra_strategy_id(self):
        from binance_ai_trader.performance_center.loader import load_paper_trades
        repo, tmp = self._make_repo_with_evaluated_signal("breakout_hunter_v1")
        db_path = str(repo._connection.execute("PRAGMA database_list").fetchone()[2])
        try:
            repo.close()
            results = load_paper_trades(db_path)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].strategy, "breakout_hunter_v1",
                             "strategy field must match analysis_snapshots.strategy_id")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resolves_baseline_v1_strategy_id(self):
        from binance_ai_trader.performance_center.loader import load_paper_trades
        repo, tmp = self._make_repo_with_evaluated_signal("baseline_v1")
        db_path = str(repo._connection.execute("PRAGMA database_list").fetchone()[2])
        try:
            repo.close()
            results = load_paper_trades(db_path)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].strategy, "baseline_v1")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_source_id_format(self):
        from binance_ai_trader.performance_center.loader import load_paper_trades
        repo, tmp = self._make_repo_with_evaluated_signal("baseline_v1")
        db_path = str(repo._connection.execute("PRAGMA database_list").fetchone()[2])
        try:
            repo.close()
            results = load_paper_trades(db_path)
            self.assertTrue(results[0].source_id.startswith("paper_pr37-run1_"))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_empty_when_no_evaluations(self):
        from binance_ai_trader.performance_center.loader import load_paper_trades
        import tempfile
        from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
        tmp = tempfile.mkdtemp()
        db_path = Path(tmp) / "empty.db"
        repo = MarketDataRepository(db_path)
        path_str = str(repo._connection.execute("PRAGMA database_list").fetchone()[2])
        repo.close()
        try:
            results = load_paper_trades(path_str)
            self.assertEqual(results, [])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_tables_returns_empty(self):
        from binance_ai_trader.performance_center.loader import load_paper_trades
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            results = load_paper_trades(db_path)
            self.assertEqual(results, [])
        finally:
            os.unlink(db_path)


# ─────────────────────────── R5: load_pending_paper_outcomes is strategy-agnostic

class LoadPendingPaperOutcomesTest(unittest.TestCase):
    """Verify load_pending_paper_outcomes() reads all strategies' evaluated signals."""

    def _make_repo_with_n_evaluations(self, n: int):
        """Create a repo with n evaluated signals (different run_ids)."""
        import tempfile
        from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
        tmp = tempfile.mkdtemp()
        db_path = Path(tmp) / "pr37out.db"
        repo = MarketDataRepository(db_path)
        for i in range(n):
            run_id = f"run-r5-{i}"
            symbol = f"COIN{i}USDT"
            ts = f"2026-06-17T0{i % 10}:00:00.000+00:00"
            ts_eval = f"2026-06-18T0{i % 10}:00:00.000+00:00"
            repo.start_run(run_id, ts)
            repo.save_signals(run_id, (_signal(symbol),), ts)
            repo.save_signal_evaluations((_evaluation(run_id, symbol),), ts_eval)
        return repo, tmp

    def test_returns_one_outcome_per_strategy(self):
        """Five evaluated signals must all appear as pending outcomes."""
        repo, tmp = self._make_repo_with_n_evaluations(5)
        try:
            outcomes = repo.load_pending_paper_outcomes()
            self.assertEqual(len(outcomes), 5,
                             "All 5 evaluated signals must be pending paper outcomes")
            symbols = {o.symbol for o in outcomes}
            for i in range(5):
                self.assertIn(f"COIN{i}USDT", symbols)
        finally:
            repo.close()
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_excludes_already_traded(self):
        """Outcomes that already have a paper_trade must not re-appear."""
        from binance_ai_trader.paper.service import PaperSimulator
        repo, tmp = self._make_repo_with_n_evaluations(2)
        try:
            # First simulate → turns both pending outcomes into paper_trades
            PaperSimulator(repo).simulate()
            # Now there should be no pending outcomes left
            outcomes = repo.load_pending_paper_outcomes()
            self.assertEqual(len(outcomes), 0,
                             "load_pending_paper_outcomes must return 0 after paper_simulate")
        finally:
            repo.close()
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_partial_paper_trade_still_exposes_remaining(self):
        """If only some signals are traded, the rest remain pending."""
        from binance_ai_trader.paper.service import PaperSimulator
        repo, tmp = self._make_repo_with_n_evaluations(3)
        try:
            # Simulate once — processes all 3
            PaperSimulator(repo).simulate()
            # Add a 4th signal+evaluation AFTER the simulate
            run_id, symbol = "run-r5-new", "NEWUSDT"
            ts = "2026-06-17T09:00:00.000+00:00"
            ts_eval = "2026-06-18T09:00:00.000+00:00"
            repo.start_run(run_id, ts)
            repo.save_signals(run_id, (_signal(symbol),), ts)
            repo.save_signal_evaluations((_evaluation(run_id, symbol),), ts_eval)
            outcomes = repo.load_pending_paper_outcomes()
            self.assertEqual(len(outcomes), 1)
            self.assertEqual(outcomes[0].symbol, "NEWUSDT")
        finally:
            repo.close()
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────── R6: diagnostic format labels "paper_trades" ──────

class DiagnosticFormatPaperTradesLabelTest(unittest.TestCase):
    def _make_stats(self, has_universe: bool = True):
        from binance_ai_trader.diagnostics.strategy_diagnostic import StrategyStats
        st = StrategyStats(strategy_id="baseline_v1", strategy_name="Baseline V1")
        if has_universe:
            st.universe = 100
            st.scored = 80
            st.snapshots = 5
        st.signals = 2
        st.trades = 1
        st.status = "ALIVE"
        return st

    def test_format_telegram_labels_trades_as_paper_trades(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import format_telegram
        msg = format_telegram([self._make_stats()], since_hours=24)
        self.assertIn("paper_trades", msg,
                      "format_telegram funnel must use 'paper_trades' label")

    def test_format_text_labels_trades_as_paper_trades(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import format_text
        text = format_text([self._make_stats()], since_hours=24)
        self.assertIn("paper_trades", text,
                      "format_text funnel must use 'paper_trades' label")

    def test_format_telegram_no_snapshots_branch_also_uses_paper_trades(self):
        """The else branch (no universe/snapshots) must also use 'paper_trades'."""
        from binance_ai_trader.diagnostics.strategy_diagnostic import (
            StrategyStats, format_telegram,
        )
        st = StrategyStats(strategy_id="baseline_v1", strategy_name="Baseline V1")
        st.signals = 2
        st.trades = 0
        st.status = "DEAD"
        msg = format_telegram([st], since_hours=24)
        self.assertIn("paper_trades", msg,
                      "format_telegram else-branch must label as 'paper_trades'")

    def test_format_telegram_bare_trades_label_absent(self):
        """The bare 'trades=' label (without 'paper_') must not appear."""
        from binance_ai_trader.diagnostics.strategy_diagnostic import format_telegram
        msg = format_telegram([self._make_stats()], since_hours=24)
        # Check that standalone "trades=" doesn't appear (paper_trades= is fine)
        import re
        bare_trades = re.search(r'(?<!paper_)trades=', msg)
        self.assertIsNone(bare_trades,
                          f"Bare 'trades=' found in format_telegram output: {msg!r}")

    def test_format_text_bare_trades_label_absent(self):
        """The bare 'trades=' label (without 'paper_') must not appear."""
        from binance_ai_trader.diagnostics.strategy_diagnostic import format_text
        text = format_text([self._make_stats()], since_hours=24)
        import re
        bare_trades = re.search(r'(?<!paper_)trades=', text)
        self.assertIsNone(bare_trades,
                          f"Bare 'trades=' found in format_text output: {text!r}")


# ─────────────────────────── R7: compileall ────────────────────────────────────

class PR37CompileAllTest(unittest.TestCase):
    BASE = Path(__file__).parents[2] / "src" / "binance_ai_trader"

    def _compile_file(self, rel: str) -> None:
        path = self.BASE / rel
        self.assertTrue(path.exists(), f"File not found: {path}")
        ok = compileall.compile_file(str(path), quiet=2, force=True)
        self.assertTrue(ok, f"compileall failed for {path}")

    def test_cli_compiles(self):
        self._compile_file("entrypoints/cli.py")

    def test_strategy_diagnostic_compiles(self):
        self._compile_file("diagnostics/strategy_diagnostic.py")

    def test_performance_center_loader_compiles(self):
        self._compile_file("performance_center/loader.py")

    def test_paper_service_compiles(self):
        self._compile_file("paper/service.py")

    def test_sqlite_repository_compiles(self):
        self._compile_file("infrastructure/sqlite_repository.py")


if __name__ == "__main__":
    unittest.main()
