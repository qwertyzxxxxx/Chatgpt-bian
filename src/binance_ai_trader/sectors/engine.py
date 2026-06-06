from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from binance_ai_trader.domain.models import SectorMember, SectorSnapshot


SECTORS = frozenset(
    {
        "AI_AGENT",
        "RWA",
        "MEME",
        "DEPIN",
        "INFRA",
        "LAYER1",
        "LAYER2",
        "DEFI",
        "GAMEFI",
        "OTHER",
    }
)


@dataclass(frozen=True, slots=True)
class SectorMap:
    symbol_to_sector: Mapping[str, str]

    def __post_init__(self) -> None:
        invalid = sorted(set(self.symbol_to_sector.values()) - SECTORS)
        if invalid:
            raise ValueError(f"unsupported sectors: {', '.join(invalid)}")
        invalid_symbols = sorted(symbol for symbol in self.symbol_to_sector if not symbol.endswith("USDT"))
        if invalid_symbols:
            raise ValueError(f"sector symbols must end in USDT: {', '.join(invalid_symbols)}")

    def sector_for(self, symbol: str) -> str:
        return self.symbol_to_sector.get(symbol, "OTHER")


class SectorStrengthEngine:
    def calculate(
        self,
        run_id: str,
        members: Iterable[SectorMember],
        sector_map: SectorMap,
    ) -> tuple[SectorSnapshot, ...]:
        grouped: dict[str, list[SectorMember]] = defaultdict(list)
        for member in members:
            grouped[sector_map.sector_for(member.symbol)].append(member)

        snapshots = [self._summarize(run_id, sector, items) for sector, items in grouped.items()]
        snapshots.sort(
            key=lambda item: (
                -item.top3_avg_score,
                -item.median_score,
                -item.avg_score,
                -item.positive_24h_ratio,
                -item.quote_volume_24h,
                item.sector,
            )
        )
        return tuple(
            SectorSnapshot(
                run_id=item.run_id,
                sector=item.sector,
                sector_rank=rank,
                member_count=item.member_count,
                avg_score=item.avg_score,
                median_score=item.median_score,
                top3_avg_score=item.top3_avg_score,
                positive_24h_ratio=item.positive_24h_ratio,
                quote_volume_24h=item.quote_volume_24h,
            )
            for rank, item in enumerate(snapshots, start=1)
        )

    @staticmethod
    def _summarize(run_id: str, sector: str, members: list[SectorMember]) -> SectorSnapshot:
        scores = [Decimal(str(member.score)) for member in members]
        top_scores = sorted(scores, reverse=True)[:3]
        positive = sum(member.change_24h > 0 for member in members)
        count = len(members)
        return SectorSnapshot(
            run_id=run_id,
            sector=sector,
            sector_rank=0,
            member_count=count,
            avg_score=_two_places(sum(scores, Decimal("0")) / Decimal(count)),
            median_score=_two_places(Decimal(str(median(scores)))),
            top3_avg_score=_two_places(sum(top_scores, Decimal("0")) / Decimal(len(top_scores))),
            positive_24h_ratio=_four_places(Decimal(positive) / Decimal(count)),
            quote_volume_24h=sum((member.quote_volume_24h for member in members), Decimal("0")),
        )


def _two_places(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _four_places(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
