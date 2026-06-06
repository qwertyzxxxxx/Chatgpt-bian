from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from binance_ai_trader.domain.models import SignalResult
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.signals import (
    RegimeSignalGate,
    SectorSignalGate,
    ShortSignalEngine,
    SignalCandidate,
    SignalEngine,
)


class SignalGenerator:
    def __init__(
        self,
        repository: MarketDataRepository,
        engine: SignalEngine | None = None,
        regime_gate: RegimeSignalGate | None = None,
        sector_map: SectorMap | None = None,
        sector_gate: SectorSignalGate | None = None,
        short_engine: ShortSignalEngine | None = None,
    ) -> None:
        self._repository = repository
        self._engine = engine or SignalEngine()
        self._short_engine = short_engine or ShortSignalEngine(self._engine.policy)
        self._regime_gate = regime_gate or RegimeSignalGate()
        self._sector_map = sector_map or SectorMap({})
        self._sector_gate = sector_gate or SectorSignalGate()

    def generate_latest(self) -> SignalResult:
        ranked_scores = self._repository.load_latest_scores(limit=20)
        if not ranked_scores:
            return SignalResult(run_id=None, signals=(), processed_symbols=0)

        run_id = ranked_scores[0].run_id
        combined_regime = self._repository.load_latest_combined_regime()
        sector_ranks = self._repository.load_sector_ranks(run_id)
        snapshots_available = bool(sector_ranks)
        opportunities = []
        for ranked in ranked_scores:
            sector = self._sector_map.sector_for(ranked.score.symbol)
            sector_rank = sector_ranks.get(sector)
            weakness_score = round(100.0 - ranked.score.score, 2)
            for direction in self._regime_gate.allowed_directions(
                combined_regime, ranked.score.score, weakness_score
            ):
                signal_score = ranked.score.score if direction == "LONG" else weakness_score
                if direction == "LONG" and not self._sector_gate.allows_long(
                    sector, sector_rank, signal_score, snapshots_available
                ):
                    continue
                opportunities.append((ranked, direction, signal_score, sector, sector_rank))

        opportunities.sort(
            key=lambda item: self._opportunity_key(
                item, snapshots_available, combined_regime
            )
        )
        signals = []
        processed_symbols: set[str] = set()
        for ranked, direction, signal_score, sector, sector_rank in opportunities:
            processed_symbols.add(ranked.score.symbol)
            engine = self._engine if direction == "LONG" else self._short_engine
            klines = {
                interval: self._repository.load_klines(ranked.score.symbol, interval, limit=limit)
                for interval, limit in engine.kline_limits.items()
            }
            score = replace(
                ranked.score,
                score=signal_score,
                score_breakdown=(
                    ranked.score.score_breakdown
                    if direction == "LONG"
                    else {
                        **ranked.score.score_breakdown,
                        "weakness": {
                            "score": signal_score,
                            "source_strength_score": ranked.score.score,
                        },
                    }
                ),
            )
            try:
                signal = engine.generate(
                    SignalCandidate(score=score, tick_size=ranked.tick_size, klines=klines)
                )
            except ValueError:
                signal = None
            if signal is not None:
                signals.append(
                    replace(
                        signal,
                        combined_regime=combined_regime,
                        sector=sector,
                        sector_rank=sector_rank,
                    )
                )
            if len(signals) == 3:
                break

        result = SignalResult(
            run_id=run_id,
            signals=tuple(signals),
            processed_symbols=len(processed_symbols),
        )
        self._repository.save_signals(run_id, result.signals, _utc_now())
        return result

    @staticmethod
    def _opportunity_key(
        item: tuple[object, str, float, str, int | None],
        snapshots: bool,
        combined_regime: str,
    ):
        ranked, direction, signal_score, _sector, sector_rank = item
        rank = ranked.rank
        symbol = ranked.score.symbol
        if not snapshots or combined_regime == "RANGE":
            return (-signal_score, 0 if direction == "LONG" else 1, rank, symbol)
        if direction == "LONG":
            sector_priority = sector_rank if sector_rank is not None else 10_000
        else:
            sector_priority = -sector_rank if sector_rank is not None else 10_000
        return (sector_priority, -signal_score, 0 if direction == "LONG" else 1, rank, symbol)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
