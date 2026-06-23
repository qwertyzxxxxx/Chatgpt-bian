"""Unit tests for strategy_diagnostic.py — all 9 PR requirements covered."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


# ─────────────────────────── DB helpers ─────────────────────────────────────

def _make_market_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE collection_runs (
            id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'SUCCEEDED',
            universe_size INTEGER, kline_count INTEGER, error_summary TEXT,
            data_quality_status TEXT
        );
        CREATE TABLE universe_snapshots (
            run_id TEXT NOT NULL, symbol TEXT NOT NULL, base_asset TEXT,
            quote_asset TEXT, contract_type TEXT, contract_status TEXT,
            volume_24h TEXT, change_24h TEXT, tick_size TEXT, step_size TEXT,
            observed_at TEXT
        );
        CREATE TABLE scores (
            run_id TEXT NOT NULL, rank INTEGER, symbol TEXT NOT NULL,
            score TEXT, score_breakdown_json TEXT, algorithm_version TEXT,
            created_at TEXT, data_quality_status TEXT,
            PRIMARY KEY (run_id, symbol)
        );
        CREATE TABLE analysis_snapshots (
            snapshot_id TEXT PRIMARY KEY, snapshot_type TEXT NOT NULL,
            collection_run_id TEXT, source_ref TEXT NOT NULL,
            data_cutoff_ms INTEGER NOT NULL DEFAULT 0,
            strategy_id TEXT NOT NULL DEFAULT 'baseline_v1',
            created_at TEXT NOT NULL, finalized_at TEXT
        );
        CREATE TABLE signals (
            run_id TEXT NOT NULL, snapshot_id TEXT,
            rank INTEGER NOT NULL DEFAULT 1, symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            combined_regime TEXT NOT NULL DEFAULT 'BULL',
            sector TEXT NOT NULL DEFAULT 'OTHER', sector_rank INTEGER,
            score REAL NOT NULL DEFAULT 50, capital_score REAL NOT NULL DEFAULT 50,
            space_score REAL NOT NULL DEFAULT 50, final_signal_score REAL NOT NULL DEFAULT 50,
            entry TEXT NOT NULL DEFAULT '1.0', latest_close TEXT NOT NULL DEFAULT '1.0',
            stop_loss TEXT NOT NULL DEFAULT '0.95', stop_loss_pct TEXT NOT NULL DEFAULT '5',
            tp1 TEXT NOT NULL DEFAULT '1.05', tp2 TEXT NOT NULL DEFAULT '1.1',
            rr_tp1 TEXT NOT NULL DEFAULT '1', rr_tp2 TEXT NOT NULL DEFAULT '2',
            logic_summary TEXT NOT NULL DEFAULT 'test', generated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, symbol)
        );
        CREATE TABLE paper_trades (
            signal_run_id TEXT NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG', result TEXT NOT NULL DEFAULT 'OPEN',
            risk_pct TEXT NOT NULL DEFAULT '1', risk_amount TEXT NOT NULL DEFAULT '10',
            realized_r TEXT, pnl TEXT, equity_after TEXT NOT NULL DEFAULT '1000',
            action TEXT NOT NULL DEFAULT 'OPEN', processed_at TEXT NOT NULL
        );
        CREATE TABLE hotlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE hotlist_outcomes (
            opportunity_id INTEGER, horizon_hours INTEGER,
            status TEXT NOT NULL, evaluated_at TEXT NOT NULL, return_pct TEXT
        );
        CREATE TABLE strategy_results (
            result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, entry TEXT, stop_loss TEXT,
            tp1 TEXT, tp2 TEXT, opened_at TEXT NOT NULL, closed_at TEXT,
            result TEXT NOT NULL DEFAULT 'OPEN',
            pnl_pct TEXT, rr_realized REAL, duration_minutes INTEGER, source_id TEXT
        );
        CREATE TABLE gemini_committee_reviews (
            review_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            provider TEXT, decision TEXT, best_symbol TEXT,
            direction TEXT, entry TEXT, stop_loss TEXT, tp1 TEXT, tp2 TEXT,
            rr TEXT, rating TEXT, risk_level TEXT, should_trade INTEGER,
            data_quality TEXT, raw_prompt_hash TEXT, raw_response TEXT,
            status TEXT NOT NULL DEFAULT 'SENT'
        );
        CREATE TABLE market_regimes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT, btc_regime TEXT, eth_regime TEXT,
            combined_regime TEXT NOT NULL, evaluated_at TEXT NOT NULL,
            data_quality_status TEXT
        );
    """)
    con.commit()
    con.close()


def _ts(offset_hours: float = 0) -> str:
    return (datetime.now(UTC) - timedelta(hours=offset_hours)).isoformat(timespec="seconds")


def _insert_signal(con: sqlite3.Connection, run_id: str, symbol: str,
                   strategy_id: str, generated_at: str | None = None) -> None:
    now = generated_at or _ts()
    snap_id = f"snap-{run_id}-{strategy_id}"
    con.execute(
        "INSERT OR IGNORE INTO analysis_snapshots"
        " (snapshot_id, snapshot_type, source_ref, strategy_id, created_at)"
        " VALUES (?,?,?,?,?)",
        (snap_id, "SCAN", run_id, strategy_id, now),
    )
    con.execute(
        "INSERT OR IGNORE INTO signals (run_id, snapshot_id, symbol, generated_at)"
        " VALUES (?,?,?,?)",
        (run_id, snap_id, symbol, now),
    )


# ─────────────────────────── Req 1: all 8 strategy_ids registered ────────────

class RegistryTest(unittest.TestCase):
    def test_all_eight_strategy_ids_in_registry(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import STRATEGY_REGISTRY
        expected = {
            "hotlist",
            "baseline_v1",
            "breakout_hunter_v1",
            "bear_short_space80_v1",
            "capital_60_80_space80_v1",
            "range_disabled_v1",
            "ai_macro",
            "gemini_committee",
        }
        self.assertEqual(set(STRATEGY_REGISTRY.keys()), expected)

    def test_registry_has_name_and_type(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import STRATEGY_REGISTRY
        for sid, meta in STRATEGY_REGISTRY.items():
            self.assertIn("name", meta, f"{sid} missing 'name'")
            self.assertIn("type", meta, f"{sid} missing 'type'")

    def test_registry_types_are_valid(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import STRATEGY_REGISTRY
        valid_types = {"scan", "hotlist", "ai_macro", "gemini"}
        for sid, meta in STRATEGY_REGISTRY.items():
            self.assertIn(meta["type"], valid_types, f"{sid} has unknown type {meta['type']}")


# ─────────────────────────── Req 2: each strategy has output ─────────────────

class FormatOutputTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_run_diagnostics_returns_eight_entries(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        stats = run_diagnostics(self._db, since_hours=24)
        self.assertEqual(len(stats), 8)

    def test_format_text_contains_all_strategy_ids(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import (
            STRATEGY_REGISTRY,
            format_text,
            run_diagnostics,
        )
        stats = run_diagnostics(self._db, since_hours=24)
        text = format_text(stats, since_hours=24)
        for sid in STRATEGY_REGISTRY:
            self.assertIn(sid, text, f"strategy_id '{sid}' not found in format_text output")

    def test_format_telegram_contains_all_strategy_ids(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import (
            STRATEGY_REGISTRY,
            format_telegram,
            run_diagnostics,
        )
        stats = run_diagnostics(self._db, since_hours=24)
        msg = format_telegram(stats, since_hours=24)
        for sid in STRATEGY_REGISTRY:
            self.assertIn(sid, msg, f"strategy_id '{sid}' not found in format_telegram output")

    def test_stats_strategy_ids_match_registry(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import (
            STRATEGY_REGISTRY,
            run_diagnostics,
        )
        stats = run_diagnostics(self._db, since_hours=24)
        ids = {s.strategy_id for s in stats}
        self.assertEqual(ids, set(STRATEGY_REGISTRY.keys()))


# ─────────────────────────── Req 3: DEAD shows breakpoint reason ─────────────

class DeadReasonTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_snapshots_reason_for_dead_scan_strategy(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        stats = run_diagnostics(self._db, since_hours=24)
        # breakout_hunter_v1 has no snapshots in empty DB
        bh = next(s for s in stats if s.strategy_id == "breakout_hunter_v1")
        self.assertEqual(bh.status, "DEAD")
        self.assertIn("no_snapshots", bh.dead_reasons)

    def test_filters_too_strict_when_snapshots_and_scores_but_no_signals(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        con = sqlite3.connect(self._db)
        now = _ts()
        # Add a collection run with scores
        con.execute(
            "INSERT INTO collection_runs (id, started_at, status) VALUES (?,?,?)",
            ("run1", now, "SUCCEEDED"),
        )
        con.execute(
            "INSERT INTO scores (run_id, symbol) VALUES (?,?)", ("run1", "BTCUSDT")
        )
        # Add snapshot for breakout_hunter_v1 but NO signals
        con.execute(
            "INSERT INTO analysis_snapshots"
            " (snapshot_id, snapshot_type, source_ref, strategy_id, created_at)"
            " VALUES (?,?,?,?,?)",
            ("snap1", "SCAN", "run1", "breakout_hunter_v1", now),
        )
        con.commit()
        con.close()

        stats = run_diagnostics(self._db, since_hours=24)
        bh = next(s for s in stats if s.strategy_id == "breakout_hunter_v1")
        self.assertEqual(bh.status, "DEAD")
        self.assertIn("filters_too_strict", bh.dead_reasons)

    def test_dead_reason_present_in_telegram_format(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import (
            format_telegram,
            run_diagnostics,
        )
        stats = run_diagnostics(self._db, since_hours=24)
        msg = format_telegram(stats, since_hours=24)
        # At least one dead reason string should appear
        self.assertIn("no_snapshots", msg)

    def test_all_dead_scan_strategies_have_reasons(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        dead_scan = [
            "breakout_hunter_v1",
            "bear_short_space80_v1",
            "capital_60_80_space80_v1",
            "range_disabled_v1",
        ]
        stats = run_diagnostics(self._db, since_hours=24)
        for sid in dead_scan:
            st = next(s for s in stats if s.strategy_id == sid)
            self.assertEqual(st.status, "DEAD", f"{sid} should be DEAD in empty DB")
            self.assertTrue(st.dead_reasons, f"{sid} should have a dead_reason")


# ─────────────────────────── Req 4: baseline signals>0 trades=0 ──────────────

class BaselineWeakTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_signals_not_traded_when_signals_but_no_paper_trades(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        con = sqlite3.connect(self._db)
        now = _ts()
        _insert_signal(con, "run1", "BTCUSDT", "baseline_v1", now)
        con.commit()
        con.close()

        stats = run_diagnostics(self._db, since_hours=24)
        bl = next(s for s in stats if s.strategy_id == "baseline_v1")
        self.assertGreater(bl.signals, 0)
        self.assertEqual(bl.trades, 0)
        self.assertIn("signals_not_traded", bl.dead_reasons)

    def test_baseline_weak_not_dead_when_has_signals(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        con = sqlite3.connect(self._db)
        now = _ts()
        _insert_signal(con, "run1", "BTCUSDT", "baseline_v1", now)
        con.commit()
        con.close()

        stats = run_diagnostics(self._db, since_hours=24)
        bl = next(s for s in stats if s.strategy_id == "baseline_v1")
        self.assertEqual(bl.status, "WEAK")

    def test_baseline_alive_when_has_signals_and_paper_trades(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        con = sqlite3.connect(self._db)
        now = _ts()
        _insert_signal(con, "run1", "BTCUSDT", "baseline_v1", now)
        con.execute(
            "INSERT INTO paper_trades"
            " (signal_run_id, symbol, direction, processed_at, equity_after, action, result,"
            " risk_pct, risk_amount)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ("run1", "BTCUSDT", "LONG", now, "1000", "OPEN", "OPEN", "1", "10"),
        )
        con.commit()
        con.close()

        stats = run_diagnostics(self._db, since_hours=24)
        bl = next(s for s in stats if s.strategy_id == "baseline_v1")
        self.assertEqual(bl.status, "ALIVE")
        self.assertEqual(bl.dead_reasons, [])


# ─────────────────────────── Req 5: hotlist shows ALIVE ──────────────────────

class HotlistAliveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_hotlist_alive_with_alerts_and_results(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        con = sqlite3.connect(self._db)
        now = _ts()
        con.execute(
            "INSERT INTO hotlist_alerts (symbol, direction, entry, created_at)"
            " VALUES (?,?,?,?)",
            ("BTCUSDT", "LONG", "30000", now),
        )
        con.execute(
            "INSERT INTO strategy_results"
            " (strategy, symbol, direction, opened_at, result)"
            " VALUES (?,?,?,?,?)",
            ("hotlist", "BTCUSDT", "LONG", now, "TP1_HIT"),
        )
        con.commit()
        con.close()

        stats = run_diagnostics(self._db, since_hours=24)
        hl = next(s for s in stats if s.strategy_id == "hotlist")
        self.assertEqual(hl.status, "ALIVE")
        self.assertEqual(hl.dead_reasons, [])

    def test_hotlist_dead_when_no_alerts(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        stats = run_diagnostics(self._db, since_hours=24)
        hl = next(s for s in stats if s.strategy_id == "hotlist")
        self.assertEqual(hl.status, "DEAD")
        self.assertIn("no_signals", hl.dead_reasons)

    def test_hotlist_weak_with_alerts_but_no_trades(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        con = sqlite3.connect(self._db)
        now = _ts()
        con.execute(
            "INSERT INTO hotlist_alerts (symbol, direction, entry, created_at)"
            " VALUES (?,?,?,?)",
            ("ETHUSDT", "LONG", "2000", now),
        )
        con.commit()
        con.close()

        stats = run_diagnostics(self._db, since_hours=24)
        hl = next(s for s in stats if s.strategy_id == "hotlist")
        self.assertEqual(hl.status, "WEAK")


# ─────────────────────────── Req 6: Telegram formatter contains strategy_id ──

class TelegramFormatterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_telegram_format_contains_strategy_id_label(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import (
            format_telegram,
            run_diagnostics,
        )
        stats = run_diagnostics(self._db, since_hours=24)
        msg = format_telegram(stats, since_hours=24)
        self.assertIn("strategy_id:", msg)
        self.assertIn("baseline_v1", msg)
        self.assertIn("hotlist", msg)
        self.assertIn("gemini_committee", msg)

    def test_telegram_format_contains_status(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import (
            format_telegram,
            run_diagnostics,
        )
        stats = run_diagnostics(self._db, since_hours=24)
        msg = format_telegram(stats, since_hours=24)
        self.assertIn("状态:", msg)

    def test_telegram_format_contains_disclaimer(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import (
            format_telegram,
            run_diagnostics,
        )
        stats = run_diagnostics(self._db, since_hours=24)
        msg = format_telegram(stats, since_hours=24)
        self.assertIn("仅供研究", msg)


# ─────────────────────────── Req 7: --since-hours works ─────────────────────

class SinceHoursTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_since_1h_excludes_old_signals(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        con = sqlite3.connect(self._db)
        old = _ts(offset_hours=48)
        _insert_signal(con, "run1", "BTCUSDT", "baseline_v1", old)
        con.commit()
        con.close()

        stats = run_diagnostics(self._db, since_hours=1)
        bl = next(s for s in stats if s.strategy_id == "baseline_v1")
        self.assertEqual(bl.signals, 0, "Signal from 48h ago should be excluded with since_hours=1")

    def test_since_24h_includes_recent_signals(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        con = sqlite3.connect(self._db)
        recent = _ts(offset_hours=1)
        _insert_signal(con, "run1", "BTCUSDT", "baseline_v1", recent)
        con.commit()
        con.close()

        stats = run_diagnostics(self._db, since_hours=24)
        bl = next(s for s in stats if s.strategy_id == "baseline_v1")
        self.assertEqual(bl.signals, 1)

    def test_since_168h_counts_7day_signals(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import run_diagnostics
        con = sqlite3.connect(self._db)
        five_days_ago = _ts(offset_hours=120)
        _insert_signal(con, "run1", "BTCUSDT", "baseline_v1", five_days_ago)
        con.commit()
        con.close()

        stats_24 = run_diagnostics(self._db, since_hours=24)
        stats_168 = run_diagnostics(self._db, since_hours=168)
        bl_24 = next(s for s in stats_24 if s.strategy_id == "baseline_v1")
        bl_168 = next(s for s in stats_168 if s.strategy_id == "baseline_v1")
        self.assertEqual(bl_24.signals, 0)
        self.assertEqual(bl_168.signals, 1)

    def test_format_text_shows_since_hours(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import (
            format_text,
            run_diagnostics,
        )
        stats = run_diagnostics(self._db, since_hours=168)
        text = format_text(stats, since_hours=168)
        self.assertIn("168", text)


# ─────────────────────────── Req 8 implied: StrategyStats dataclass ──────────

class StrategyStatsTest(unittest.TestCase):
    def test_dataclass_defaults(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import StrategyStats
        st = StrategyStats(strategy_id="test", strategy_name="Test")
        self.assertEqual(st.signals, 0)
        self.assertEqual(st.trades, 0)
        self.assertEqual(st.status, "DEAD")
        self.assertEqual(st.dead_reasons, [])

    def test_win_rate_none_when_no_results(self):
        from binance_ai_trader.diagnostics.strategy_diagnostic import StrategyStats
        st = StrategyStats(strategy_id="test", strategy_name="Test")
        self.assertIsNone(st.win_rate)
        self.assertIsNone(st.avg_rr)


# ─────────────────────────── Req 9: compileall ───────────────────────────────

class CompileAllTest(unittest.TestCase):
    def test_diagnostics_module_compiles(self):
        import compileall
        import os
        pkg = os.path.join(
            os.path.dirname(__file__),
            "../../src/binance_ai_trader/diagnostics",
        )
        ok = compileall.compile_dir(pkg, quiet=2, force=True)
        self.assertTrue(ok, "compileall failed on diagnostics package")

    def test_hourly_report_compiles(self):
        import compileall
        import os
        f = os.path.join(
            os.path.dirname(__file__),
            "../../src/binance_ai_trader/runner/hourly_strategy_report.py",
        )
        ok = compileall.compile_file(f, quiet=2, force=True)
        self.assertTrue(ok)

    def test_startup_report_compiles(self):
        import compileall
        import os
        f = os.path.join(
            os.path.dirname(__file__),
            "../../src/binance_ai_trader/runner/startup_report.py",
        )
        ok = compileall.compile_file(f, quiet=2, force=True)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
