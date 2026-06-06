import unittest

from binance_ai_trader.signals import SectorSignalGate


class SectorSignalGateTest(unittest.TestCase):
    def test_strong_sector_allows_candidate_without_score_threshold(self) -> None:
        gate = SectorSignalGate()
        self.assertTrue(gate.allows_long("LAYER1", 1, 1, True))
        self.assertTrue(gate.allows_long("LAYER1", 3, 1, True))

    def test_medium_sector_requires_score_of_at_least_85(self) -> None:
        gate = SectorSignalGate()
        self.assertFalse(gate.allows_long("DEFI", 4, 84.99, True))
        self.assertTrue(gate.allows_long("DEFI", 6, 85, True))

    def test_weak_or_other_sector_requires_score_of_at_least_90(self) -> None:
        gate = SectorSignalGate()
        self.assertFalse(gate.allows_long("GAMEFI", 7, 89.99, True))
        self.assertTrue(gate.allows_long("GAMEFI", 7, 90, True))
        self.assertFalse(gate.allows_long("OTHER", 1, 89.99, True))
        self.assertTrue(gate.allows_long("OTHER", 1, 90, True))

    def test_missing_sector_snapshots_do_not_block(self) -> None:
        gate = SectorSignalGate()
        self.assertTrue(gate.allows_long("OTHER", None, 1, False))


if __name__ == "__main__":
    unittest.main()
