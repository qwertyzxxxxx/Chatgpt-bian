"""Tests for PR #44: split hotlist performance by rank_type (GAINER/LOSER/VOLUME/UNKNOWN)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.hotlist.models import (
    HotlistAIReview,
    HotlistAlert,
    HotlistEntryPlan,
    HotlistWatchlistItem,
    TrackedHotlistOpportunity,
)
from binance_ai_trader.hotlist.ai_review import review_hotlist_opportunities
from binance_ai_trader.hotlist.alerts import HotlistAlertEngine
from binance_ai_trader.hotlist.repository import HotlistWatchlistRepository
from binance_ai_trader.runner.hourly_strategy_report import (
    _query_candidate_perf_by_source,
    _query_push_perf_by_source,
    _query_last_7_pushed_orders,
    _table_exists,
)

NOW = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_plan(symbol: str, direction: str = "LONG") -> HotlistEntryPlan:
    entry = Decimal("100")
    stop = Decimal("95") if direction == "LONG" else Decimal("105")
    sign = Decimal("1") if direction == "LONG" else Decimal("-1")
    risk = abs(entry - stop)
    return HotlistEntryPlan(
        symbol=symbol,
        direction=direction,
        current_price=entry,
        change_24h_pct=Decimal("20"),
        quote_volume=Decimal("10000000"),
        volume_ratio_15m=Decimal("2"),
        ema20_15m=entry,
        atr14=Decimal("2"),
        swing_high=entry + Decimal("5"),
        swing_low=entry - Decimal("5"),
        suggested_limit_entry=entry,
        stop_loss=stop,
        tp1=entry + sign * risk,
        tp2=entry + sign * risk * Decimal("3"),
        rr=Decimal("3"),
        expires_at=(NOW + timedelta(minutes=60)).isoformat(timespec="seconds"),
        reason="Test plan.",
    )


def _make_watchlist_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE hotlist_watchlist (
            symbol TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'GAINER',
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            last_rank INTEGER NOT NULL DEFAULT 1,
            observation_count INTEGER NOT NULL DEFAULT 1,
            first_seen_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE hotlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry TEXT NOT NULL,
            created_at TEXT NOT NULL,
            stop_loss TEXT,
            tp1 TEXT,
            tp2 TEXT,
            rr TEXT,
            expires_at TEXT,
            rank_type TEXT DEFAULT 'UNKNOWN'
        );
        CREATE TABLE hotlist_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry TEXT NOT NULL,
            stop_loss TEXT NOT NULL,
            tp1 TEXT NOT NULL,
            tp2 TEXT NOT NULL,
            rr TEXT NOT NULL,
            confidence TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            rank_type TEXT DEFAULT 'UNKNOWN'
        );
        CREATE TABLE hotlist_outcomes (
            opportunity_id INTEGER,
            horizon_hours INTEGER,
            status TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            return_pct TEXT
        );
        CREATE TABLE strategy_results (
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry TEXT NOT NULL,
            strategy TEXT NOT NULL,
            result TEXT,
            pnl_pct REAL,
            rr_realized REAL,
            duration_minutes INTEGER,
            closed_at TEXT
        );
    """)
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Model-level tests
# ---------------------------------------------------------------------------

class TestModelRankTypeField(unittest.TestCase):
    def test_hotlist_ai_review_has_rank_type(self):
        r = HotlistAIReview(
            symbol="BTCUSDT", direction="LONG", entry=Decimal("100"),
            stop_loss=Decimal("95"), tp1=Decimal("105"), tp2=Decimal("115"),
            rr=Decimal("3"), confidence="HIGH", reason="test",
            expires_at="2026-06-14T12:00:00", rank_type="GAINER",
        )
        self.assertEqual(r.rank_type, "GAINER")

    def test_hotlist_ai_review_default_rank_type(self):
        r = HotlistAIReview(
            symbol="BTCUSDT", direction="LONG", entry=Decimal("100"),
            stop_loss=Decimal("95"), tp1=Decimal("105"), tp2=Decimal("115"),
            rr=Decimal("3"), confidence="HIGH", reason="test",
            expires_at="2026-06-14T12:00:00",
        )
        self.assertEqual(r.rank_type, "UNKNOWN")

    def test_tracked_opportunity_has_rank_type(self):
        opp = TrackedHotlistOpportunity(
            id=None, symbol="ETHUSDT", direction="SHORT",
            entry=Decimal("200"), stop_loss=Decimal("210"),
            tp1=Decimal("190"), tp2=Decimal("180"),
            rr=Decimal("2"), confidence="MEDIUM",
            created_at="2026-06-14T12:00:00",
            expires_at="2026-06-14T13:00:00",
            rank_type="LOSER",
        )
        self.assertEqual(opp.rank_type, "LOSER")

    def test_hotlist_alert_has_rank_type(self):
        plan = _make_plan("XYZUSDT")
        alert = HotlistAlert(
            symbol="XYZUSDT", direction="LONG",
            entry=Decimal("100"), created_at="2026-06-14T12:00:00",
            level="A", plan=plan, rank_type="VOLUME",
        )
        self.assertEqual(alert.rank_type, "VOLUME")

    def test_hotlist_alert_default_rank_type(self):
        plan = _make_plan("XYZUSDT")
        alert = HotlistAlert(
            symbol="XYZUSDT", direction="LONG",
            entry=Decimal("100"), created_at="2026-06-14T12:00:00",
            level="A", plan=plan,
        )
        self.assertEqual(alert.rank_type, "UNKNOWN")


# ---------------------------------------------------------------------------
# ai_review rank_type derivation
# ---------------------------------------------------------------------------

class TestAiReviewRankType(unittest.TestCase):
    def test_long_plan_gets_gainer(self):
        plans = (
            _make_plan("BTCUSDT", "LONG"),
            _make_plan("ETHUSDT", "LONG"),
        )
        reviews = review_hotlist_opportunities(plans, limit=2)
        for r in reviews:
            self.assertEqual(r.rank_type, "GAINER", f"{r.symbol} should be GAINER")

    def test_short_plan_gets_loser(self):
        plans = (_make_plan("XRPUSDT", "SHORT"),)
        reviews = review_hotlist_opportunities(plans, limit=1)
        for r in reviews:
            self.assertEqual(r.rank_type, "LOSER", f"{r.symbol} should be LOSER")


# ---------------------------------------------------------------------------
# alerts.py: rank_type propagation from watchlist source
# ---------------------------------------------------------------------------

class FakeOpportunityReview:
    def __init__(self, plans):
        self._plans = plans

    def review(self, now=None):
        return tuple(self._plans)


class TestAlertEngineRankType(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "market.db"
        # Pre-populate watchlist via direct sqlite before repo init
        ts = NOW.isoformat(timespec="seconds")
        exp = (NOW + timedelta(hours=24)).isoformat(timespec="seconds")
        pre = sqlite3.connect(str(self._db_path))
        pre.executescript("""
            CREATE TABLE IF NOT EXISTS hotlist_watchlist (
                symbol TEXT PRIMARY KEY, source TEXT NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, observation_count INTEGER NOT NULL,
                last_rank INTEGER NOT NULL, status TEXT NOT NULL
            );
        """)
        pre.executemany(
            "INSERT INTO hotlist_watchlist(symbol, source, status, last_rank, observation_count, first_seen_at, last_seen_at, expires_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                ("BTCUSDT", "GAINER", "ACTIVE", 1, 3, ts, ts, exp),
                ("XRPUSDT", "LOSER", "ACTIVE", 2, 3, ts, ts, exp),
                ("DOTUSDT", "VOLUME", "ACTIVE", 3, 3, ts, ts, exp),
            ],
        )
        pre.commit()
        pre.close()
        self._repo = HotlistWatchlistRepository(self._db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_gainer_watchlist_propagates_to_alert(self):
        plans = [_make_plan("BTCUSDT", "LONG")]
        engine = HotlistAlertEngine(FakeOpportunityReview(plans), self._repo)
        alerts, _, _ = engine.generate(NOW)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].rank_type, "GAINER")

    def test_loser_watchlist_propagates_to_alert(self):
        plans = [_make_plan("XRPUSDT", "SHORT")]
        engine = HotlistAlertEngine(FakeOpportunityReview(plans), self._repo)
        alerts, _, _ = engine.generate(NOW)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].rank_type, "LOSER")

    def test_volume_watchlist_propagates_to_alert(self):
        plans = [_make_plan("DOTUSDT", "LONG")]
        engine = HotlistAlertEngine(FakeOpportunityReview(plans), self._repo)
        alerts, _, _ = engine.generate(NOW)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].rank_type, "VOLUME")


# ---------------------------------------------------------------------------
# repository: rank_type persisted to DB
# ---------------------------------------------------------------------------

class TestRepositoryRankTypePersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "market.db"
        ts = NOW.isoformat(timespec="seconds")
        exp = (NOW + timedelta(hours=24)).isoformat(timespec="seconds")
        pre = sqlite3.connect(str(self._db_path))
        pre.executescript("""
            CREATE TABLE IF NOT EXISTS hotlist_watchlist (
                symbol TEXT PRIMARY KEY, source TEXT NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, observation_count INTEGER NOT NULL,
                last_rank INTEGER NOT NULL, status TEXT NOT NULL
            );
        """)
        pre.execute(
            "INSERT INTO hotlist_watchlist(symbol, source, status, last_rank, observation_count, first_seen_at, last_seen_at, expires_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("BTCUSDT", "GAINER", "ACTIVE", 1, 3, ts, ts, exp),
        )
        pre.commit()
        pre.close()
        self._repo = HotlistWatchlistRepository(self._db_path)
        # Direct connection for assertions
        self._con = sqlite3.connect(str(self._db_path))

    def tearDown(self):
        self._con.close()
        self._tmp.cleanup()

    def test_save_alert_persists_rank_type(self):
        plan = _make_plan("BTCUSDT", "LONG")
        alert = HotlistAlert(
            symbol="BTCUSDT", direction="LONG",
            entry=Decimal("100"), created_at=NOW.isoformat(timespec="seconds"),
            level="A", plan=plan, rank_type="GAINER",
        )
        self._repo.save_alert(alert)
        row = self._con.execute(
            "SELECT rank_type FROM hotlist_alerts WHERE symbol='BTCUSDT'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "GAINER")

    def test_save_alert_unknown_rank_type(self):
        plan = _make_plan("BTCUSDT", "LONG")
        alert = HotlistAlert(
            symbol="BTCUSDT", direction="LONG",
            entry=Decimal("100"), created_at=NOW.isoformat(timespec="seconds"),
            level="A", plan=plan,
        )
        self._repo.save_alert(alert)
        row = self._con.execute(
            "SELECT rank_type FROM hotlist_alerts WHERE symbol='BTCUSDT'"
        ).fetchone()
        self.assertEqual(row[0], "UNKNOWN")


# ---------------------------------------------------------------------------
# hourly_strategy_report: per-source queries
# ---------------------------------------------------------------------------

def _make_report_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE hotlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry TEXT NOT NULL, created_at TEXT NOT NULL,
            rank_type TEXT DEFAULT 'UNKNOWN'
        );
        CREATE TABLE hotlist_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry TEXT NOT NULL, stop_loss TEXT, tp1 TEXT, tp2 TEXT, rr TEXT,
            confidence TEXT, created_at TEXT NOT NULL, expires_at TEXT,
            rank_type TEXT DEFAULT 'UNKNOWN'
        );
        CREATE TABLE hotlist_outcomes (
            opportunity_id INTEGER,
            horizon_hours INTEGER,
            status TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            return_pct TEXT
        );
        CREATE TABLE strategy_results (
            symbol TEXT NOT NULL, direction TEXT NOT NULL, entry TEXT NOT NULL,
            strategy TEXT NOT NULL, result TEXT,
            pnl_pct REAL, rr_realized REAL, duration_minutes INTEGER, closed_at TEXT
        );
    """)
    return con


class TestQueryPushPerfBySource(unittest.TestCase):
    def setUp(self):
        self._con = _make_report_db()
        since = "2026-06-13T00:00:00"
        self._con.execute(
            "INSERT INTO hotlist_alerts(symbol, direction, entry, created_at, rank_type) VALUES (?,?,?,?,?)",
            ("BTCUSDT", "LONG", "100", "2026-06-14T10:00:00", "GAINER"),
        )
        self._con.execute(
            "INSERT INTO hotlist_alerts(symbol, direction, entry, created_at, rank_type) VALUES (?,?,?,?,?)",
            ("XRPUSDT", "SHORT", "200", "2026-06-14T10:00:00", "LOSER"),
        )
        self._con.execute(
            "INSERT INTO strategy_results(symbol, direction, entry, strategy, result) VALUES (?,?,?,?,?)",
            ("BTCUSDT", "LONG", "100", "hotlist", "TP1"),
        )
        self._con.execute(
            "INSERT INTO strategy_results(symbol, direction, entry, strategy, result) VALUES (?,?,?,?,?)",
            ("XRPUSDT", "SHORT", "200", "hotlist", "SL"),
        )
        self._con.commit()

    def tearDown(self):
        self._con.close()

    def test_gainer_push_perf(self):
        r = _query_push_perf_by_source(self._con, "2026-06-13T00:00:00", "GAINER")
        self.assertEqual(r["tp1"], 1)
        self.assertEqual(r["sl"], 0)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["win_rate"], 100)

    def test_loser_push_perf(self):
        r = _query_push_perf_by_source(self._con, "2026-06-13T00:00:00", "LOSER")
        self.assertEqual(r["sl"], 1)
        self.assertEqual(r["tp1"], 0)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["win_rate"], 0)

    def test_volume_push_perf_empty(self):
        r = _query_push_perf_by_source(self._con, "2026-06-13T00:00:00", "VOLUME")
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["win_rate"], 0)


class TestQueryCandidatePerfBySource(unittest.TestCase):
    def setUp(self):
        self._con = _make_report_db()
        self._con.execute(
            "INSERT INTO hotlist_opportunities(symbol, direction, entry, stop_loss, tp1, tp2, rr, confidence, created_at, expires_at, rank_type)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("BTCUSDT", "LONG", "100", "95", "105", "115", "3", "HIGH", "2026-06-14T10:00:00", "2026-06-14T11:00:00", "GAINER"),
        )
        self._con.execute(
            "INSERT INTO hotlist_opportunities(symbol, direction, entry, stop_loss, tp1, tp2, rr, confidence, created_at, expires_at, rank_type)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("XRPUSDT", "SHORT", "50", "55", "45", "40", "2", "MEDIUM", "2026-06-14T10:00:00", "2026-06-14T11:00:00", "LOSER"),
        )
        self._con.execute(
            "INSERT INTO hotlist_outcomes(opportunity_id, horizon_hours, status, evaluated_at) VALUES (?,?,?,?)",
            (1, 4, "TP1_HIT", "2026-06-14T12:00:00"),
        )
        self._con.execute(
            "INSERT INTO hotlist_outcomes(opportunity_id, horizon_hours, status, evaluated_at) VALUES (?,?,?,?)",
            (2, 4, "SL_HIT", "2026-06-14T12:00:00"),
        )
        self._con.commit()

    def tearDown(self):
        self._con.close()

    def test_gainer_candidate_perf(self):
        r = _query_candidate_perf_by_source(self._con, "2026-06-13T00:00:00", "GAINER")
        self.assertEqual(r["tp1"], 1)
        self.assertEqual(r["sl"], 0)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["win_rate"], 100)

    def test_loser_candidate_perf(self):
        r = _query_candidate_perf_by_source(self._con, "2026-06-13T00:00:00", "LOSER")
        self.assertEqual(r["sl"], 1)
        self.assertEqual(r["tp1"], 0)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["win_rate"], 0)

    def test_volume_candidate_perf_empty(self):
        r = _query_candidate_perf_by_source(self._con, "2026-06-13T00:00:00", "VOLUME")
        self.assertEqual(r["total"], 0)


class TestQueryLast7WithRankType(unittest.TestCase):
    def setUp(self):
        self._con = _make_report_db()
        self._con.execute(
            "INSERT INTO hotlist_alerts(symbol, direction, entry, created_at, rank_type) VALUES (?,?,?,?,?)",
            ("BTCUSDT", "LONG", "100", "2026-06-14T10:00:00", "GAINER"),
        )
        self._con.execute(
            "INSERT INTO hotlist_alerts(symbol, direction, entry, created_at, rank_type) VALUES (?,?,?,?,?)",
            ("XRPUSDT", "SHORT", "50", "2026-06-14T09:00:00", "LOSER"),
        )
        self._con.commit()

    def tearDown(self):
        self._con.close()

    def test_rank_type_in_last_7(self):
        rows = _query_last_7_pushed_orders(self._con)
        self.assertEqual(len(rows), 2)
        # sorted by created_at DESC → BTCUSDT first
        self.assertEqual(rows[0]["symbol"], "BTCUSDT")
        self.assertEqual(rows[0]["rank_type"], "GAINER")
        self.assertEqual(rows[1]["symbol"], "XRPUSDT")
        self.assertEqual(rows[1]["rank_type"], "LOSER")


if __name__ == "__main__":
    unittest.main()
