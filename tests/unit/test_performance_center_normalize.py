import unittest
from binance_ai_trader.performance_center.models import (
    normalize_result,
    RESULT_OPEN, RESULT_TP1, RESULT_TP2, RESULT_SL, RESULT_TIMEOUT, RESULT_EXPIRED,
)


class TestNormalizeResult(unittest.TestCase):
    def test_canonical_passthrough(self):
        for v in (RESULT_OPEN, RESULT_TP1, RESULT_TP2, RESULT_SL, RESULT_TIMEOUT, RESULT_EXPIRED):
            self.assertEqual(normalize_result(v), v)

    def test_win_alias(self):
        self.assertEqual(normalize_result("WIN"), RESULT_TP1)

    def test_win_tp1_alias(self):
        self.assertEqual(normalize_result("WIN_TP1"), RESULT_TP1)

    def test_tp1_hit_alias(self):
        self.assertEqual(normalize_result("TP1_HIT"), RESULT_TP1)

    def test_profit_alias(self):
        self.assertEqual(normalize_result("PROFIT"), RESULT_TP1)

    def test_closed_profit_alias(self):
        self.assertEqual(normalize_result("CLOSED_PROFIT"), RESULT_TP1)

    def test_win_tp2_alias(self):
        self.assertEqual(normalize_result("WIN_TP2"), RESULT_TP2)

    def test_tp2_hit_alias(self):
        self.assertEqual(normalize_result("TP2_HIT"), RESULT_TP2)

    def test_loss_alias(self):
        self.assertEqual(normalize_result("LOSS"), RESULT_SL)

    def test_closed_loss_alias(self):
        self.assertEqual(normalize_result("CLOSED_LOSS"), RESULT_SL)

    def test_loss_sl_alias(self):
        self.assertEqual(normalize_result("LOSS_SL"), RESULT_SL)

    def test_expired_canonical(self):
        self.assertEqual(normalize_result("EXPIRED"), RESULT_EXPIRED)

    def test_timeout_canonical(self):
        self.assertEqual(normalize_result("TIMEOUT"), RESULT_TIMEOUT)

    def test_case_insensitive(self):
        self.assertEqual(normalize_result("win"), RESULT_TP1)
        self.assertEqual(normalize_result("Loss"), RESULT_SL)
        self.assertEqual(normalize_result("closed_profit"), RESULT_TP1)

    def test_none_returns_open(self):
        self.assertEqual(normalize_result(None), RESULT_OPEN)

    def test_empty_string_returns_open(self):
        self.assertEqual(normalize_result(""), RESULT_OPEN)

    def test_whitespace_returns_open(self):
        self.assertEqual(normalize_result("   "), RESULT_OPEN)

    def test_unknown_passthrough(self):
        self.assertEqual(normalize_result("SOME_UNKNOWN_VALUE"), "SOME_UNKNOWN_VALUE")

    def test_open_alias(self):
        self.assertEqual(normalize_result("OPEN"), RESULT_OPEN)


if __name__ == "__main__":
    unittest.main()
