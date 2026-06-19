import sqlite3
import tempfile
import unittest
from pathlib import Path

from binance_ai_trader.gemini_committee.candidate_builder import (
    build_candidates,
    load_hotlist_alert_candidates,
    load_hotlist_candidates,
    merge_top_n,
)
from binance_ai_trader.gemini_committee.models import Candidate


def _cand(symbol: str, source: str = "hotlist") -> Candidate:
    return Candidate(
        symbol=symbol, source=source, direction="LONG",
        entry="100", stop_loss="95", tp1="110", tp2="120", rr="2.00"
    )


def _make_db_with_opportunities(db_path: str) -> None:
    """Create DB with hotlist_opportunities rows (full data)."""
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS hotlist_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry TEXT NOT NULL, sl TEXT NOT NULL,
            tp1 TEXT NOT NULL, tp2 TEXT NOT NULL, rr TEXT NOT NULL,
            confidence TEXT NOT NULL, created_at TEXT NOT NULL,
            expiry TEXT NOT NULL,
            UNIQUE(symbol, direction, entry, created_at)
        );
        CREATE TABLE IF NOT EXISTS hotlist_watchlist (
            symbol TEXT PRIMARY KEY, source TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            expires_at TEXT NOT NULL, observation_count INTEGER NOT NULL,
            last_rank INTEGER NOT NULL, status TEXT NOT NULL
        );
    """)
    # Insert opportunity with expiry far in the future
    con.execute(
        "INSERT INTO hotlist_opportunities "
        "(symbol, direction, entry, sl, tp1, tp2, rr, confidence, created_at, expiry) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("LABUSDT", "LONG", "0.05", "0.045", "0.06", "0.07", "2.00",
         "STRONG", "2026-06-17T00:00:00+00:00", "2099-12-31"),
    )
    con.commit()
    con.close()


def _make_db_with_alerts_only(db_path: str) -> None:
    """Create DB with hotlist_alerts but empty hotlist_opportunities."""
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS hotlist_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry TEXT NOT NULL, sl TEXT NOT NULL,
            tp1 TEXT NOT NULL, tp2 TEXT NOT NULL, rr TEXT NOT NULL,
            confidence TEXT NOT NULL, created_at TEXT NOT NULL,
            expiry TEXT NOT NULL,
            UNIQUE(symbol, direction, entry, created_at)
        );
        CREATE TABLE IF NOT EXISTS hotlist_watchlist (
            symbol TEXT PRIMARY KEY, source TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            expires_at TEXT NOT NULL, observation_count INTEGER NOT NULL,
            last_rank INTEGER NOT NULL, status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hotlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry TEXT NOT NULL, created_at TEXT NOT NULL
        );
    """)
    from datetime import UTC, datetime, timedelta
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    con.execute(
        "INSERT INTO hotlist_alerts (symbol, direction, entry, created_at) VALUES (?, ?, ?, ?)",
        ("SIRENUSDT", "LONG", "0.10", recent),
    )
    con.execute(
        "INSERT INTO hotlist_alerts (symbol, direction, entry, created_at) VALUES (?, ?, ?, ?)",
        ("GWEIUSDT", "SHORT", "1.00", recent),
    )
    con.commit()
    con.close()


class MergeTopNTest(unittest.TestCase):
    def test_hotlist_takes_priority(self):
        hotlist = [_cand("A"), _cand("B")]
        ai = [_cand("C", "ai_macro"), _cand("D", "ai_macro")]
        result = merge_top_n(hotlist, ai, max_n=4)
        self.assertEqual([c.symbol for c in result], ["A", "B", "C", "D"])

    def test_capped_at_max_n(self):
        hotlist = [_cand(f"H{i}") for i in range(6)]
        ai = [_cand(f"A{i}", "ai_macro") for i in range(3)]
        result = merge_top_n(hotlist, ai, max_n=4)
        self.assertEqual(len(result), 4)

    def test_deduplication_keeps_hotlist_version(self):
        hotlist = [_cand("BTCUSDT")]
        ai = [_cand("BTCUSDT", "ai_macro")]
        result = merge_top_n(hotlist, ai, max_n=4)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source, "hotlist")

    def test_empty_both_returns_empty(self):
        result = merge_top_n([], [], max_n=4)
        self.assertEqual(result, [])

    def test_only_ai_macro(self):
        ai = [_cand("X", "ai_macro"), _cand("Y", "ai_macro")]
        result = merge_top_n([], ai, max_n=4)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].source, "ai_macro")


class StopPctTest(unittest.TestCase):
    def test_correct_calculation(self):
        from binance_ai_trader.gemini_committee.candidate_builder import _stop_pct
        result = _stop_pct("100.0", "95.0")
        self.assertAlmostEqual(float(result), 5.0, places=1)

    def test_unknown_on_bad_input(self):
        from binance_ai_trader.gemini_committee.candidate_builder import _stop_pct
        self.assertEqual(_stop_pct("0", "95"), "UNKNOWN")
        self.assertEqual(_stop_pct("bad", "95"), "UNKNOWN")


class LoadHotlistCandidatesTest(unittest.TestCase):
    """load_hotlist_candidates reads hotlist_opportunities."""

    def test_returns_candidates_when_opportunities_exist(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        _make_db_with_opportunities(db_path)
        result = load_hotlist_candidates(db_path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].symbol, "LABUSDT")
        self.assertEqual(result[0].source, "hotlist")
        self.assertEqual(result[0].data_quality, "FULL")

    def test_returns_empty_when_table_missing(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        result = load_hotlist_candidates(db_path)
        self.assertEqual(result, [])


class LoadHotlistAlertCandidatesTest(unittest.TestCase):
    """load_hotlist_alert_candidates fallback from hotlist_alerts."""

    def test_returns_candidates_from_alerts(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        _make_db_with_alerts_only(db_path)
        result = load_hotlist_alert_candidates(db_path)
        self.assertGreater(len(result), 0)

    def test_alert_candidates_have_partial_data_quality(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        _make_db_with_alerts_only(db_path)
        result = load_hotlist_alert_candidates(db_path)
        for c in result:
            self.assertEqual(c.data_quality, "PARTIAL")

    def test_alert_candidates_unknown_sl_tp_rr(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        _make_db_with_alerts_only(db_path)
        result = load_hotlist_alert_candidates(db_path)
        for c in result:
            self.assertEqual(c.stop_loss, "UNKNOWN")
            self.assertEqual(c.tp1, "UNKNOWN")
            self.assertEqual(c.tp2, "UNKNOWN")
            self.assertEqual(c.rr, "UNKNOWN")

    def test_alert_candidates_source_is_hotlist_alert(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        _make_db_with_alerts_only(db_path)
        result = load_hotlist_alert_candidates(db_path)
        for c in result:
            self.assertEqual(c.source, "hotlist_alert")

    def test_returns_empty_when_no_alerts(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        result = load_hotlist_alert_candidates(db_path)
        self.assertEqual(result, [])

    def test_deduplicates_same_symbol(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        con = sqlite3.connect(db_path)
        con.execute("""CREATE TABLE hotlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, direction TEXT, entry TEXT, created_at TEXT)""")
        from datetime import UTC, datetime, timedelta
        t1 = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        t2 = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        con.execute("INSERT INTO hotlist_alerts VALUES (?,?,?,?,?)",
                    (None, "LABUSDT", "LONG", "0.05", t1))
        con.execute("INSERT INTO hotlist_alerts VALUES (?,?,?,?,?)",
                    (None, "LABUSDT", "LONG", "0.052", t2))
        con.commit()
        con.close()
        result = load_hotlist_alert_candidates(db_path)
        symbols = [c.symbol for c in result]
        self.assertEqual(symbols.count("LABUSDT"), 1)


class BuildCandidatesFallbackTest(unittest.TestCase):
    """build_candidates falls back to hotlist_alerts when opportunities empty."""

    def test_uses_opportunities_when_available(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        _make_db_with_opportunities(db)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            ai_db = f.name

        from unittest.mock import patch
        with patch("binance_ai_trader.gemini_committee.candidate_builder._load_ticker_map",
                   return_value={}), \
             patch("binance_ai_trader.gemini_committee.candidate_builder._enrich_klines",
                   side_effect=lambda c, _: c):
            result = build_candidates(db, ai_db, max_candidates=4)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].symbol, "LABUSDT")
        self.assertEqual(result[0].source, "hotlist")

    def test_falls_back_to_alerts_when_opportunities_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        _make_db_with_alerts_only(db)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            ai_db = f.name

        from unittest.mock import patch
        with patch("binance_ai_trader.gemini_committee.candidate_builder._load_ticker_map",
                   return_value={}), \
             patch("binance_ai_trader.gemini_committee.candidate_builder._enrich_klines",
                   side_effect=lambda c, _: c):
            result = build_candidates(db, ai_db, max_candidates=4)

        self.assertGreater(len(result), 0)
        for c in result:
            self.assertEqual(c.source, "hotlist_alert")
            self.assertEqual(c.data_quality, "PARTIAL")

    def test_fallback_candidates_have_unknown_fields(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        _make_db_with_alerts_only(db)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            ai_db = f.name

        from unittest.mock import patch
        with patch("binance_ai_trader.gemini_committee.candidate_builder._load_ticker_map",
                   return_value={}), \
             patch("binance_ai_trader.gemini_committee.candidate_builder._enrich_klines",
                   side_effect=lambda c, _: c):
            result = build_candidates(db, ai_db, max_candidates=4)

        for c in result:
            self.assertEqual(c.stop_loss, "UNKNOWN")
            self.assertEqual(c.tp1, "UNKNOWN")
            self.assertEqual(c.rr, "UNKNOWN")

    def test_candidate_data_quality_full_in_to_dict(self):
        c = _cand("XUSDT")
        d = c.to_dict()
        self.assertEqual(d["data_quality"], "FULL")

    def test_partial_candidate_data_quality_in_to_dict(self):
        c = Candidate(
            symbol="YUSDT", source="hotlist_alert", direction="LONG",
            entry="1.0", stop_loss="UNKNOWN", tp1="UNKNOWN", tp2="UNKNOWN",
            rr="UNKNOWN", data_quality="PARTIAL",
        )
        d = c.to_dict()
        self.assertEqual(d["data_quality"], "PARTIAL")


if __name__ == "__main__":
    unittest.main()
