import sqlite3
import tempfile
import unittest

from binance_ai_trader.gemini_committee.candidate_builder import merge_top_n
from binance_ai_trader.gemini_committee.models import Candidate


def _cand(symbol: str, source: str = "hotlist") -> Candidate:
    return Candidate(
        symbol=symbol, source=source, direction="LONG",
        entry="100", stop_loss="95", tp1="110", tp2="120", rr="2.00"
    )


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


if __name__ == "__main__":
    unittest.main()
