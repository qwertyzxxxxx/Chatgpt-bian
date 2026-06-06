from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from binance_ai_trader.domain.models import SignalResult
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.signals import (
    RegimeSignalGate,
    SectorSignalGate,
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
    ) -> None:
        self._repository = repository
        self._engine = engine or SignalEngine()
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
        candidate_contexts = [
            (
                ranked,
                self._sector_map.sector_for(ranked.score.symbol),
            )
            for ranked in ranked_scores
        ]
        if snapshots_available:
            candidate_contexts.sort(
                key=lambda item: (
                    sector_ranks.get(item[1], 10_000),
                    item[0].rank,
                    item[0].score.symbol,
                )
            )
        signals = []
        processed_symbols = 0
        for ranked, sector in candidate_contexts:
            processed_symbols += 1
            sector_rank = sector_ranks.get(sector)
            if not self._regime_gate.allows_long(combined_regime, ranked.score.score):
                continue
            if not self._sector_gate.allows_long(
                sector,
                sector_rank,
                ranked.score.score,
                snapshots_available,
            ):
                continue
            klines = {
                interval: self._repository.load_klines(ranked.score.symbol, interval, limit=limit)
                for interval, limit in self._engine.kline_limits.items()
            }
            try:
                signal = self._engine.generate(
                    SignalCandidate(score=ranked.score, tick_size=ranked.tick_size, klines=klines)
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

        result = SignalResult(run_id=run_id, signals=tuple(signals), processed_symbols=processed_symbols)
        self._repository.save_signals(run_id, result.signals, _utc_now())
        return result


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
