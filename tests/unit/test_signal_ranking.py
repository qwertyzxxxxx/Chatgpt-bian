import unittest

from binance_ai_trader.signals.ranking import final_signal_score


class FinalSignalScoreTest(unittest.TestCase):
    def test_applies_required_v2_weights(self) -> None:
        score = final_signal_score(
            capital_score=80, space_score=60, trend_score=70,
            sector_rank=2, combined_regime="BULL", direction="LONG",
        )
        self.assertEqual(76.0, score)

    def test_short_uses_downtrend_and_weak_sector_directionally(self) -> None:
        score = final_signal_score(
            capital_score=80, space_score=80, trend_score=20,
            sector_rank=8, combined_regime="BEAR", direction="SHORT",
        )
        self.assertEqual(84.0, score)
