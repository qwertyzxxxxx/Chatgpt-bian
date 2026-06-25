"""Unit tests for leaderboard_watch/diagnostics.py."""
from __future__ import annotations

import unittest

from binance_ai_trader.leaderboard_watch.diagnostics import (
    EXPECTED_TIMEFRAMES,
    EXPECTED_TF_FIELDS,
    analyze_candidate_fields,
    top_missing_fields,
)


def _make_candidate(
    symbol: str = "BTCUSDT",
    change: str = "10.5",
    volume: str = "5000000",
    rank_type: str = "GAINER",
    m15: dict | None = None,
    h1: dict | None = None,
    h4: dict | None = None,
    d1: dict | None = None,
) -> dict:
    full_tf = {
        "trend": "UP",
        "rsi14": 55.0,
        "atr_pct": 1.5,
        "volume_ratio": 1.2,
        "recent_high": 30000.0,
        "recent_low": 28000.0,
    }
    return {
        "symbol": symbol,
        "rank_type": rank_type,
        "change_24h": change,
        "quote_volume": volume,
        "m15": m15 if m15 is not None else dict(full_tf),
        "h1": h1 if h1 is not None else dict(full_tf),
        "h4": h4 if h4 is not None else dict(full_tf),
        "d1": d1 if d1 is not None else dict(full_tf),
    }


class TestAnalyzeCandidateFieldsEmpty(unittest.TestCase):
    def test_empty_list(self):
        result = analyze_candidate_fields([])
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["unknown_ratio"], 0.0)
        self.assertEqual(result["total_fields"], 0)
        self.assertEqual(result["unknown_fields"], 0)


class TestAnalyzeCandidateFieldsFullData(unittest.TestCase):
    def test_perfect_candidate_zero_unknown(self):
        cand = _make_candidate()
        result = analyze_candidate_fields([cand])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["unknown_ratio"], 0.0)
        self.assertEqual(result["unknown_fields"], 0)

    def test_multiple_perfect_candidates(self):
        cands = [_make_candidate(symbol=f"SYM{i}") for i in range(5)]
        result = analyze_candidate_fields(cands)
        self.assertEqual(result["candidate_count"], 5)
        self.assertEqual(result["unknown_ratio"], 0.0)


class TestAnalyzeCandidateFieldsMissing(unittest.TestCase):
    def test_missing_tf_data_counts_unknown(self):
        """All timeframe data missing → high unknown_ratio."""
        cand = _make_candidate(m15={}, h1={}, h4={}, d1={})
        result = analyze_candidate_fields([cand])
        self.assertGreater(result["unknown_ratio"], 0.5)

    def test_unknown_string_counted(self):
        cand = _make_candidate(
            m15={"trend": "UNKNOWN", "rsi14": "UNKNOWN",
                 "atr_pct": "UNKNOWN", "volume_ratio": "UNKNOWN",
                 "recent_high": "UNKNOWN", "recent_low": "UNKNOWN"},
            h1={"trend": "UNKNOWN", "rsi14": "UNKNOWN",
                "atr_pct": "UNKNOWN", "volume_ratio": "UNKNOWN",
                "recent_high": "UNKNOWN", "recent_low": "UNKNOWN"},
            h4={"trend": "UNKNOWN", "rsi14": "UNKNOWN",
                "atr_pct": "UNKNOWN", "volume_ratio": "UNKNOWN",
                "recent_high": "UNKNOWN", "recent_low": "UNKNOWN"},
            d1={"trend": "UNKNOWN", "rsi14": "UNKNOWN",
                "atr_pct": "UNKNOWN", "volume_ratio": "UNKNOWN",
                "recent_high": "UNKNOWN", "recent_low": "UNKNOWN"},
        )
        result = analyze_candidate_fields([cand])
        tf_total = len(EXPECTED_TIMEFRAMES) * len(EXPECTED_TF_FIELDS)
        self.assertEqual(result["unknown_fields"], tf_total)

    def test_missing_symbol_counted(self):
        cand = _make_candidate()
        cand.pop("symbol")
        result = analyze_candidate_fields([cand])
        self.assertIn("symbol", result["missing_field_counts"])

    def test_none_value_counted_as_unknown(self):
        cand = _make_candidate()
        cand["m15"]["trend"] = None
        result = analyze_candidate_fields([cand])
        self.assertIn("m15.trend", result["missing_field_counts"])


class TestAnalyzeRiskDistribution(unittest.TestCase):
    def test_low_risk_candidate(self):
        cand = _make_candidate(h1={"trend": "UP", "rsi14": 50, "atr_pct": 0.5,
                                    "volume_ratio": 1.0, "recent_high": 100,
                                    "recent_low": 90})
        result = analyze_candidate_fields([cand])
        self.assertEqual(result["risk_distribution"]["LOW"], 1)

    def test_medium_risk_candidate(self):
        cand = _make_candidate(h1={"trend": "UP", "rsi14": 50, "atr_pct": 3.0,
                                    "volume_ratio": 1.0, "recent_high": 100,
                                    "recent_low": 90})
        result = analyze_candidate_fields([cand])
        self.assertEqual(result["risk_distribution"]["MEDIUM"], 1)

    def test_high_risk_candidate(self):
        cand = _make_candidate(h1={"trend": "UP", "rsi14": 50, "atr_pct": 8.0,
                                    "volume_ratio": 1.0, "recent_high": 100,
                                    "recent_low": 90})
        result = analyze_candidate_fields([cand])
        self.assertEqual(result["risk_distribution"]["HIGH"], 1)

    def test_unknown_atr_classified_as_high_risk(self):
        cand = _make_candidate(h1={"trend": "UP", "rsi14": 50, "atr_pct": "UNKNOWN",
                                    "volume_ratio": 1.0, "recent_high": 100,
                                    "recent_low": 90})
        result = analyze_candidate_fields([cand])
        self.assertEqual(result["risk_distribution"]["HIGH"], 1)


class TestTopMissingFields(unittest.TestCase):
    def test_empty_returns_empty(self):
        result = top_missing_fields({})
        self.assertEqual(result, [])

    def test_returns_at_most_n(self):
        counts = {f"field{i}": i for i in range(10)}
        result = top_missing_fields(counts, n=3)
        self.assertEqual(len(result), 3)

    def test_sorted_by_count_desc(self):
        counts = {"a": 5, "b": 10, "c": 3}
        result = top_missing_fields(counts, n=3)
        self.assertEqual(result[0][0], "b")
        self.assertEqual(result[0][1], 10)

    def test_ties_sorted_by_name(self):
        counts = {"zfield": 5, "afield": 5}
        result = top_missing_fields(counts, n=5)
        self.assertEqual(result[0][0], "afield")

    def test_returns_tuples(self):
        counts = {"x": 3}
        result = top_missing_fields(counts)
        self.assertIsInstance(result[0], tuple)
        self.assertEqual(result[0], ("x", 3))
