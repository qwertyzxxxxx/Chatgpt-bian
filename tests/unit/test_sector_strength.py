from decimal import Decimal
import unittest

from binance_ai_trader.domain.models import SectorMember
from binance_ai_trader.sectors import SectorMap, SectorStrengthEngine


class SectorStrengthEngineTest(unittest.TestCase):
    def test_calculates_metrics_ranks_sectors_and_maps_unknown_to_other(self) -> None:
        members = (
            SectorMember("FETUSDT", 90, Decimal("5"), Decimal("1000")),
            SectorMember("TAOUSDT", 80, Decimal("-1"), Decimal("2000")),
            SectorMember("RENDERUSDT", 70, Decimal("2"), Decimal("3000")),
            SectorMember("DOGEUSDT", 75, Decimal("3"), Decimal("4000")),
            SectorMember("UNKNOWNUSDT", 60, Decimal("0"), Decimal("500")),
        )
        sector_map = SectorMap(
            {
                "FETUSDT": "AI_AGENT",
                "TAOUSDT": "AI_AGENT",
                "RENDERUSDT": "AI_AGENT",
                "DOGEUSDT": "MEME",
            }
        )

        snapshots = SectorStrengthEngine().calculate("run-1", members, sector_map)

        self.assertEqual(("AI_AGENT", "MEME", "OTHER"), tuple(item.sector for item in snapshots))
        ai = snapshots[0]
        self.assertEqual(1, ai.sector_rank)
        self.assertEqual(3, ai.member_count)
        self.assertEqual(Decimal("80.00"), ai.avg_score)
        self.assertEqual(Decimal("80.00"), ai.median_score)
        self.assertEqual(Decimal("80.00"), ai.top3_avg_score)
        self.assertEqual(Decimal("0.6667"), ai.positive_24h_ratio)
        self.assertEqual(Decimal("6000"), ai.quote_volume_24h)
        self.assertEqual("OTHER", sector_map.sector_for("UNKNOWNUSDT"))

    def test_top3_average_uses_only_three_highest_members(self) -> None:
        members = tuple(
            SectorMember(f"COIN{index}USDT", score, Decimal("1"), Decimal("1"))
            for index, score in enumerate((100, 90, 80, 10), start=1)
        )
        sector_map = SectorMap({member.symbol: "INFRA" for member in members})

        snapshot = SectorStrengthEngine().calculate("run-1", members, sector_map)[0]

        self.assertEqual(Decimal("70.00"), snapshot.avg_score)
        self.assertEqual(Decimal("85.00"), snapshot.median_score)
        self.assertEqual(Decimal("90.00"), snapshot.top3_avg_score)

    def test_rejects_unsupported_sector(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported sectors"):
            SectorMap({"BTCUSDT": "UNKNOWN"})


if __name__ == "__main__":
    unittest.main()
