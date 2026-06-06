import unittest

from binance_ai_trader.signals import RegimeSignalGate


class RegimeSignalGateTest(unittest.TestCase):
    def test_bull_allows_long_candidates(self) -> None:
        gate = RegimeSignalGate()
        self.assertTrue(gate.allows_long("BULL", 1))

    def test_range_requires_score_of_at_least_80(self) -> None:
        gate = RegimeSignalGate()
        self.assertFalse(gate.allows_long("RANGE", 79.99))
        self.assertTrue(gate.allows_long("RANGE", 80))

    def test_bear_observe_and_unknown_block_long_candidates(self) -> None:
        gate = RegimeSignalGate()
        self.assertFalse(gate.allows_long("BEAR", 100))
        self.assertFalse(gate.allows_long("OBSERVE", 100))
        self.assertFalse(gate.allows_long("UNKNOWN", 100))


if __name__ == "__main__":
    unittest.main()
