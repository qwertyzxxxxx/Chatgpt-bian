"""V3 Pipeline — Strategy → Risk → Dedup → Candidate → PushQueue → FeatureStore.

Usage:
    pipeline = V3Pipeline(db_path, dedup_hours=24, cross_strategy_dedup=False)
    result   = pipeline.run(strategy)

Each strategy call is fully self-contained:
  1. strategy.generate_candidates()         ← strategy's only responsibility
  2. Risk Engine check (per candidate)      ← unified gate
  3. Dedup Engine check (per candidate)     ← configurable window
  4. Save to v3_candidates (status=PUSHED)  ← permanent record
  5. Save features to v3_feature_store      ← optional, AI training
  6. Enqueue in v3_push_queue               ← sender picks up asynchronously
  7. Blocked/dup candidates saved too       ← with status BLOCKED / DEDUP
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from binance_ai_trader.v3.candidates.repository import (
    CandidateInput,
    V3Candidate,
    V3CandidateRepository,
)
from binance_ai_trader.v3.dedup.engine import V3DedupEngine
from binance_ai_trader.v3.feature_store.repository import V3FeatureStoreRepository
from binance_ai_trader.v3.push_queue.repository import V3PushQueueRepository
from binance_ai_trader.v3.risk.engine import RiskConfig, V3RiskEngine
from binance_ai_trader.v3.strategies.base import V3Strategy

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    strategy_id: str
    scanned: int = 0
    pushed: int = 0
    blocked_risk: int = 0
    blocked_dedup: int = 0
    errors: int = 0
    candidates: list[V3Candidate] = field(default_factory=list)

    @property
    def total_blocked(self) -> int:
        return self.blocked_risk + self.blocked_dedup


class V3Pipeline:
    """Runs a single strategy through the full V3 pipeline pass."""

    def __init__(
        self,
        db_path: Path | str,
        dedup_hours: int = 24,
        cross_strategy_dedup: bool = False,
        risk_config: RiskConfig | None = None,
    ) -> None:
        self._db = str(db_path)
        self._dedup_hours = dedup_hours
        self._cross_strategy_dedup = cross_strategy_dedup
        self._risk_config = risk_config

        self._candidate_repo = V3CandidateRepository(db_path)
        self._push_repo = V3PushQueueRepository(db_path)
        self._feature_repo = V3FeatureStoreRepository(db_path)
        self._risk = V3RiskEngine(db_path)
        self._dedup = V3DedupEngine(db_path)

    def run(
        self,
        strategy: V3Strategy,
        now: datetime | None = None,
        market_regime: str | None = None,
    ) -> PipelineResult:
        run_at = now or datetime.now(UTC)
        result = PipelineResult(strategy_id=strategy.strategy_id)

        try:
            candidates = strategy.generate_candidates(now=run_at)
        except Exception:
            log.exception("[V3] %s generate_candidates() failed", strategy.strategy_id)
            result.errors += 1
            return result

        result.scanned = len(candidates)
        log.info("[V3] %s: %d candidates scanned", strategy.strategy_id, result.scanned)

        for inp in candidates:
            try:
                self._process_one(inp, result, market_regime, run_at)
            except Exception:
                log.exception("[V3] error processing candidate %s/%s", inp.symbol, inp.direction)
                result.errors += 1

        log.info(
            "[V3] %s done — pushed=%d blocked_risk=%d blocked_dedup=%d errors=%d",
            strategy.strategy_id,
            result.pushed,
            result.blocked_risk,
            result.blocked_dedup,
            result.errors,
        )
        return result

    # ------------------------------------------------------------------

    def _process_one(
        self,
        inp: CandidateInput,
        result: PipelineResult,
        market_regime: str | None,
        now: datetime,
    ) -> None:
        risk_dec = self._risk.check(
            strategy_id=inp.strategy_id,
            symbol=inp.symbol,
            direction=inp.direction,
            config=self._risk_config,
            market_regime=market_regime,
        )
        if not risk_dec.allowed:
            log.info("[V3] BLOCKED(risk) %s/%s: %s", inp.symbol, inp.direction, risk_dec.reason)
            signal_id = self._candidate_repo.generate_signal_id(inp.strategy_id, now=now)
            self._candidate_repo.save(
                inp, signal_id, status="BLOCKED", reason_override=risk_dec.reason
            )
            result.blocked_risk += 1
            return

        dedup_dec = self._dedup.check(
            strategy_id=inp.strategy_id,
            symbol=inp.symbol,
            direction=inp.direction,
            window_hours=self._dedup_hours,
            cross_strategy=self._cross_strategy_dedup,
        )
        if dedup_dec.is_dup:
            log.debug("[V3] BLOCKED(dedup) %s/%s: %s", inp.symbol, inp.direction, dedup_dec.reason)
            signal_id = self._candidate_repo.generate_signal_id(inp.strategy_id, now=now)
            self._candidate_repo.save(
                inp, signal_id, status="DEDUP", reason_override=dedup_dec.reason
            )
            result.blocked_dedup += 1
            return

        signal_id = self._candidate_repo.generate_signal_id(inp.strategy_id, now=now)
        candidate = self._candidate_repo.save(inp, signal_id, status="PUSHED")

        try:
            features = _extract_features(inp)
            self._feature_repo.save(signal_id, inp.strategy_id, features)
        except Exception:
            log.warning("[V3] feature store save failed for %s", signal_id)

        if not self._push_repo.already_queued(signal_id):
            self._push_repo.enqueue(signal_id, inp.strategy_id)

        result.pushed += 1
        result.candidates.append(candidate)
        log.info(
            "[V3] PUSHED %s %s/%s entry=%s rr=%s",
            signal_id, inp.symbol, inp.direction, inp.entry, inp.rr,
        )


def _extract_features(inp: CandidateInput) -> dict:
    return {
        "strategy_id": inp.strategy_id,
        "symbol": inp.symbol,
        "direction": inp.direction,
        "entry": inp.entry,
        "sl": inp.sl,
        "tp1": inp.tp1,
        "tp2": inp.tp2,
        "rr": inp.rr,
        "confidence": inp.confidence,
        "stop_pct": inp.stop_pct,
        "change_24h": inp.change_24h,
        "quote_volume": inp.quote_volume,
        "volume_ratio": inp.volume_ratio,
        "atr": inp.atr,
        "ema20": inp.ema20,
        "ema60": inp.ema60,
        "market_regime": inp.market_regime,
        "reason": inp.reason,
    }
