import sqlite3
import tempfile
import os
import unittest
import uuid
from binance_ai_trader.performance_center.loader import (
    load_hotlist, load_ai_macro, load_gemini_committee, load_all,
)
from binance_ai_trader.performance_center.models import (
    STRATEGY_HOTLIST, STRATEGY_AI_MACRO, STRATEGY_GEMINI, RESULT_OPEN,
)


def _make_market_db(path: str):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS hotlist_opportunities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL, direction TEXT NOT NULL,
        entry TEXT NOT NULL, sl TEXT NOT NULL,
        tp1 TEXT NOT NULL, tp2 TEXT NOT NULL, rr TEXT NOT NULL,
        confidence TEXT NOT NULL, created_at TEXT NOT NULL, expiry TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS gemini_committee_reviews (
        review_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'gemini', decision TEXT NOT NULL,
        best_symbol TEXT NOT NULL, direction TEXT NOT NULL,
        entry TEXT NOT NULL, stop_loss TEXT NOT NULL,
        tp1 TEXT NOT NULL, tp2 TEXT NOT NULL, rr TEXT NOT NULL,
        rating TEXT NOT NULL, risk_level TEXT NOT NULL,
        should_trade INTEGER NOT NULL, data_quality TEXT NOT NULL,
        raw_prompt_hash TEXT NOT NULL, raw_response TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN'
    );
    INSERT INTO hotlist_opportunities (symbol, direction, entry, sl, tp1, tp2, rr, confidence, created_at, expiry)
    VALUES ('BTCUSDT','LONG','50000','48000','52000','54000','2.0','HIGH','2024-01-01T00:00:00','2024-01-02T00:00:00');
    INSERT INTO gemini_committee_reviews
    (review_id, created_at, provider, decision, best_symbol, direction, entry, stop_loss, tp1, tp2, rr, rating, risk_level, should_trade, data_quality, raw_prompt_hash, raw_response, status)
    VALUES ('rev-001','2024-01-01T00:00:00','gemini','TRADE','ETHUSDT','LONG','3000','2900','3100','3200','2.0','A','LOW',1,'GOOD','hash','{}','OPEN');
    """)
    con.commit()
    con.close()


def _make_ai_macro_db(path: str):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS ai_macro_trades (
        trade_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
        symbol TEXT NOT NULL, direction TEXT NOT NULL,
        entry TEXT NOT NULL, stop_loss TEXT NOT NULL,
        tp1 TEXT NOT NULL, tp2 TEXT NOT NULL,
        score INTEGER NOT NULL, market_state TEXT NOT NULL,
        risk_grade TEXT NOT NULL, reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        pnl_pct TEXT, closed_at TEXT
    );
    INSERT INTO ai_macro_trades
    (trade_id, created_at, symbol, direction, entry, stop_loss, tp1, tp2, score, market_state, risk_grade, reason, status)
    VALUES ('trade-001','2024-01-01T00:00:00','SOLUSDT','SHORT','200','210','190','180',80,'BEARISH','LOW','macro bearish','OPEN');
    """)
    con.commit()
    con.close()


class TestLoader(unittest.TestCase):
    def setUp(self):
        self.market_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        self.ai_macro_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        _make_market_db(self.market_db)
        _make_ai_macro_db(self.ai_macro_db)

    def tearDown(self):
        os.unlink(self.market_db)
        os.unlink(self.ai_macro_db)

    def test_load_hotlist(self):
        results = load_hotlist(self.market_db)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.strategy, STRATEGY_HOTLIST)
        self.assertEqual(r.symbol, "BTCUSDT")
        self.assertEqual(r.direction, "LONG")
        self.assertEqual(r.stop_loss, "48000")
        self.assertEqual(r.result, RESULT_OPEN)
        self.assertTrue(r.source_id.startswith("hotlist_"))

    def test_load_ai_macro(self):
        results = load_ai_macro(self.ai_macro_db)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.strategy, STRATEGY_AI_MACRO)
        self.assertEqual(r.symbol, "SOLUSDT")
        self.assertEqual(r.direction, "SHORT")
        self.assertEqual(r.source_id, "trade-001")

    def test_load_gemini_committee(self):
        results = load_gemini_committee(self.market_db)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.strategy, STRATEGY_GEMINI)
        self.assertEqual(r.symbol, "ETHUSDT")
        self.assertEqual(r.source_id, "rev-001")

    def test_load_all(self):
        results = load_all(self.market_db, self.ai_macro_db)
        strategies = {r.strategy for r in results}
        self.assertIn(STRATEGY_HOTLIST, strategies)
        self.assertIn(STRATEGY_AI_MACRO, strategies)
        self.assertIn(STRATEGY_GEMINI, strategies)
        self.assertEqual(len(results), 3)

    def test_unique_result_ids(self):
        results = load_all(self.market_db, self.ai_macro_db)
        ids = [r.result_id for r in results]
        self.assertEqual(len(ids), len(set(ids)))

    def test_missing_sl_becomes_unknown(self):
        con = sqlite3.connect(self.market_db)
        con.execute(
            "INSERT INTO hotlist_opportunities (symbol, direction, entry, sl, tp1, tp2, rr, confidence, created_at, expiry)"
            " VALUES ('XRPUSDT','LONG','1.0','','1.1','1.2','2.0','LOW','2024-01-02T00:00:00','2024-01-03T00:00:00')"
        )
        con.commit()
        con.close()
        results = load_hotlist(self.market_db)
        xrp = [r for r in results if r.symbol == "XRPUSDT"][0]
        self.assertEqual(xrp.stop_loss, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
