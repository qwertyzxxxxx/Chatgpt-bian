from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from binance_ai_trader.domain.models import SignalResult
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.signals.ranking import final_signal_score
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

    def generate_latest(self, snapshot_id: str | None = None) -> SignalResult:
        try:
            snapshot = (self._repository.load_snapshot(snapshot_id) if snapshot_id
                        else self._repository.load_latest_snapshot())
        except ValueError:
            return SignalResult(run_id=None, signals=(), processed_symbols=0)
        ranked_scores = self._repository.load_scores_for_snapshot(snapshot.snapshot_id, limit=20)
        if not ranked_scores:
            generated_at = _utc_now()
            self._repository.save_signals(
                snapshot.collection_run_id or "", (), generated_at, snapshot.snapshot_id
            )
            self._repository.finalize_snapshot(snapshot.snapshot_id, generated_at)
            return SignalResult(
                run_id=snapshot.collection_run_id, signals=(), processed_symbols=0,
                snapshot_id=snapshot.snapshot_id,
            )

        run_id = ranked_scores[0].run_id
        combined_regime = self._repository.load_combined_regime(snapshot.snapshot_id)
        sector_ranks = self._repository.load_sector_ranks(run_id)
        capital_scores = self._repository.load_capital_scores(run_id)
        space_scores = self._repository.load_space_scores(run_id)
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
                capital_score = capital_scores.get(ranked.score.symbol, 50.0)
                space_score = space_scores.get((ranked.score.symbol, direction), 50.0)
                trend_score = _component_score(ranked.score.score_breakdown, "trend", ranked.score.score)
                final_score = final_signal_score(
                    capital_score=capital_score, space_score=space_score, trend_score=trend_score,
                    sector_rank=sector_rank, combined_regime=combined_regime, direction=direction,
                )
                opportunities.append((
                    ranked, direction, signal_score, sector, sector_rank,
                    capital_score, space_score, final_score,
                ))

        opportunities.sort(key=lambda item: (-item[7], item[1], item[0].rank, item[0].score.symbol))
        signals = []
        processed_symbols: set[str] = set()
        direction_counts = {"LONG": 0, "SHORT": 0}
        for ranked, direction, signal_score, sector, sector_rank, capital_score, space_score, final_score in opportunities:
            if direction_counts[direction] >= 3:
                continue
            processed_symbols.add(ranked.score.symbol)
            engine = self._engine if direction == "LONG" else self._short_engine
            klines = {
                interval: self._repository.load_klines_at(
                    ranked.score.symbol, interval, snapshot.data_cutoff_ms, limit=limit
                )
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
                        capital_score=capital_score,
                        space_score=space_score,
                        final_signal_score=final_score,
                    )
                )
                direction_counts[direction] += 1
            if all(count >= 3 for direction, count in direction_counts.items()
                   if any(item[1] == direction for item in opportunities)):
                break

        result = SignalResult(
            run_id=run_id,
            signals=tuple(signals),
            processed_symbols=len(processed_symbols),
            snapshot_id=snapshot.snapshot_id,
        )
        generated_at = _utc_now()
        self._repository.save_signals(
            run_id, result.signals, generated_at, snapshot.snapshot_id
        )
        self._repository.finalize_snapshot(snapshot.snapshot_id, generated_at)
        return result



def _component_score(breakdown: dict[str, object], name: str, fallback: float) -> float:
    value = breakdown.get(name)
    if isinstance(value, dict) and "score" in value:
        return float(value["score"])
    if isinstance(value, (int, float)):
        return float(value)
    return fallback



def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
