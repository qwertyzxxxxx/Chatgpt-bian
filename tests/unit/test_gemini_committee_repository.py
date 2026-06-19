import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from binance_ai_trader.gemini_committee.models import Candidate, CommitteeDecision
from binance_ai_trader.gemini_committee.repository import CommitteeRepository


def _trade_decision() -> CommitteeDecision:
    return CommitteeDecision(
        decision="TRADE", best_symbol="BTCUSDT", direction="LONG",
        rating="A+", entry="50000", stop_loss="48000", tp1="52000", tp2="54000",
        rr="2.00", risk_level="LOW", should_trade=True,
        reasons=["strong"], reject_reasons=[], data_quality="GOOD"
    )


def _no_trade_decision() -> CommitteeDecision:
    return CommitteeDecision.no_trade()


def _cand(symbol: str) -> Candidate:
    return Candidate(
        symbol=symbol, source="hotlist", direction="LONG",
        entry="100", stop_loss="95", tp1="110", tp2="120", rr="2.00"
    )


class RepositorySchemaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._repo = CommitteeRepository(self._tmp.name)

    def tearDown(self):
        self._repo.close()

    def test_tables_created(self):
        import sqlite3
        con = sqlite3.connect(self._tmp.name)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        con.close()
        self.assertIn("gemini_committee_reviews", tables)
        self.assertIn("gemini_committee_candidates", tables)


class SaveReviewTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._repo = CommitteeRepository(self._tmp.name)

    def tearDown(self):
        self._repo.close()

    def test_save_and_retrieve_review(self):
        self._repo.save_review("rev-001", _trade_decision(), "hash001", "gemini-2.5-flash")
        reviews = self._repo.all_reviews()
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["review_id"], "rev-001")
        self.assertEqual(reviews[0]["decision"], "TRADE")
        self.assertEqual(reviews[0]["best_symbol"], "BTCUSDT")

    def test_save_candidates(self):
        self._repo.save_review("rev-002", _no_trade_decision(), "hash002", "gemini-2.5-flash")
        candidates = [_cand("ETHUSDT"), _cand("SOLUSDT")]
        self._repo.save_candidates("rev-002", candidates)
        import sqlite3
        con = sqlite3.connect(self._tmp.name)
        rows = con.execute("SELECT * FROM gemini_committee_candidates WHERE review_id='rev-002'").fetchall()
        con.close()
        self.assertEqual(len(rows), 2)


class CooldownTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._repo = CommitteeRepository(self._tmp.name)

    def tearDown(self):
        self._repo.close()

    def test_last_review_at_none_when_empty(self):
        self.assertIsNone(self._repo.last_review_at())

    def test_last_review_at_returns_datetime(self):
        self._repo.save_review("rev-003", _no_trade_decision(), "h", "m")
        last = self._repo.last_review_at()
        self.assertIsNotNone(last)
        self.assertIsInstance(last, datetime)


class OpenTradeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._repo = CommitteeRepository(self._tmp.name)

    def tearDown(self):
        self._repo.close()

    def test_no_open_trade_when_empty(self):
        self.assertFalse(self._repo.has_open_trade_recommendation())

    def test_has_open_trade_after_trade_decision(self):
        self._repo.save_review("rev-004", _trade_decision(), "h", "m")
        self.assertTrue(self._repo.has_open_trade_recommendation())

    def test_no_open_trade_when_only_no_trade(self):
        self._repo.save_review("rev-005", _no_trade_decision(), "h", "m")
        self.assertFalse(self._repo.has_open_trade_recommendation())


if __name__ == "__main__":
    unittest.main()
