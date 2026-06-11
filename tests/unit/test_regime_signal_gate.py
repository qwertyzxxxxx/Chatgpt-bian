import unittest

from binance_ai_trader.signals import RegimeSignalGate


class RegimeSignalGateTest(unittest.TestCase):
    def test_bull_allows_only_long(self) -> None:
        gate = RegimeSignalGate()
        self.assertTrue(gate.allows_long("BULL", 1))
        self.assertFalse(gate.allows_short("BULL", 100))
        self.assertEqual(("LONG",), gate.allowed_directions("BULL", 1, 100))

    def test_bear_allows_only_short(self) -> None:
        gate = RegimeSignalGate()
        self.assertFalse(gate.allows_long("BEAR", 100))
        self.assertTrue(gate.allows_short("BEAR", 1))
        self.assertEqual(("SHORT",), gate.allowed_directions("BEAR", 100, 1))

    def test_range_requires_score_of_at_least_85_for_both_directions(self) -> None:
        gate = RegimeSignalGate()
        self.assertFalse(gate.allows_long("RANGE", 84.99))
        self.assertTrue(gate.allows_long("RANGE", 85))
        self.assertFalse(gate.allows_short("RANGE", 84.99))
        self.assertTrue(gate.allows_short("RANGE", 85))
        self.assertEqual(("LONG", "SHORT"), gate.allowed_directions("RANGE", 85, 85))

    def test_observe_and_unknown_block_both_directions(self) -> None:
        gate = RegimeSignalGate()
        for regime in ("OBSERVE", "UNKNOWN"):
            self.assertEqual((), gate.allowed_directions(regime, 100, 100))


if __name__ == "__main__":
    unittest.main()
