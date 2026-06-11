import unittest

from binance_ai_trader.data_quality import quality_context_status, worst_quality


class DataQualityTest(unittest.TestCase):
    def test_aggregate_status_uses_documented_precedence(self) -> None:
        self.assertEqual("COMPLETE", worst_quality("COMPLETE"))
        self.assertEqual("PARTIAL", worst_quality("COMPLETE", "PARTIAL"))
        self.assertEqual("STALE", worst_quality("PARTIAL", "STALE"))
        self.assertEqual("FALLBACK", worst_quality("STALE", "FALLBACK"))
        self.assertEqual("MISSING", worst_quality("FALLBACK", "MISSING"))

    def test_context_makes_fallback_explicit(self) -> None:
        context = {
            "capital": "STALE",
            "capital_value": "FALLBACK",
            "space": "COMPLETE",
        }
        self.assertEqual("FALLBACK", quality_context_status(context))

    def test_rejects_unknown_status(self) -> None:
        with self.assertRaises(ValueError):
            worst_quality("UNKNOWN")


if __name__ == "__main__":
    unittest.main()
