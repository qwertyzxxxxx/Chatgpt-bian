import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from binance_ai_trader.gemini_committee.committee import GeminiCommittee
from binance_ai_trader.gemini_committee.models import SkipResult
from binance_ai_trader.gemini_committee.repository import CommitteeRepository


def _make_repo(db_path: str) -> CommitteeRepository:
    return CommitteeRepository(db_path)


class SkipGeminiKeyMissingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db = self._tmp.name
        self._tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db2 = self._tmp2.name

    def test_skipped_when_api_key_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            gc = GeminiCommittee(self._db, self._db2)
            result = gc.review()
            gc.close()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "gemini_api_key_missing")


class SkipExistingOpenRecommendationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db = self._tmp.name
        self._tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db2 = self._tmp2.name

    def test_skipped_when_open_trade_exists(self):
        from binance_ai_trader.gemini_committee.models import CommitteeDecision
        repo = _make_repo(self._db)
        decision = CommitteeDecision(
            decision="TRADE", best_symbol="BTCUSDT", direction="LONG",
            rating="A", entry="50000", stop_loss="48000", tp1="52000", tp2="54000",
            rr="2.00", risk_level="LOW", should_trade=True,
            reasons=["test"], reject_reasons=[], data_quality="GOOD"
        )
        repo.save_review("rev-001", decision, "abc123", "gemini-2.5-flash")
        repo.close()

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            gc = GeminiCommittee(self._db, self._db2)
            result = gc.review()
            gc.close()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "existing_open_recommendation")


class SkipCooldownActiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db = self._tmp.name
        self._tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db2 = self._tmp2.name

    def test_skipped_when_cooldown_active(self):
        from binance_ai_trader.gemini_committee.models import CommitteeDecision
        repo = _make_repo(self._db)
        decision = CommitteeDecision.no_trade()
        repo.save_review("rev-002", decision, "abc123", "gemini-2.5-flash")
        repo.close()

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            gc = GeminiCommittee(self._db, self._db2, cooldown_hours=4.0)
            result = gc.review()
            gc.close()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "cooldown_active")


class SkipNoCandidatesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db = self._tmp.name
        self._tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db2 = self._tmp2.name

    def test_skipped_when_no_candidates(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with patch("binance_ai_trader.gemini_committee.committee.build_candidates", return_value=[]):
                gc = GeminiCommittee(self._db, self._db2, cooldown_hours=0.0)
                result = gc.review()
                gc.close()
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "no_candidates")


if __name__ == "__main__":
    unittest.main()
