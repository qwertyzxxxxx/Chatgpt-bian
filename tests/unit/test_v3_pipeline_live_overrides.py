"""Pipeline.run() must honor per-call dedup_hours/risk_config overrides.

This is what lets tasks.py fetch live-adjusted settings (via
V3RuntimeSettingsRepository) each scan cycle without rebuilding the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from binance_ai_trader.v3.pipeline import V3Pipeline
from binance_ai_trader.v3.risk.engine import RiskConfig


@dataclass
class _FakeCandidateInput:
    strategy_id: str = "test_strategy"
    symbol: str = "BTCUSDT"
    direction: str = "LONG"
    entry: float = 100.0
    sl: float = 95.0
    tp1: float = 105.0
    tp2: float = 110.0
    rr: float = 2.0
    confidence: float = 1.0
    stop_pct: float = 5.0
    change_24h: float = 1.0
    quote_volume: float = 1_000_000.0
    volume_ratio: float = 1.0
    atr: float = 1.0
    ema20: float = 1.0
    ema60: float = 1.0
    market_regime: str | None = None
    reason: str = "ok"


class _FakeStrategy:
    strategy_id = "test_strategy"

    def __init__(self, candidates):
        self._candidates = candidates

    def generate_candidates(self, now=None):
        return self._candidates


def _build_pipeline():
    with patch("binance_ai_trader.v3.pipeline.V3CandidateRepository"), \
         patch("binance_ai_trader.v3.pipeline.V3PushQueueRepository"), \
         patch("binance_ai_trader.v3.pipeline.V3FeatureStoreRepository"), \
         patch("binance_ai_trader.v3.pipeline.V3RiskEngine"), \
         patch("binance_ai_trader.v3.pipeline.V3DedupEngine"):
        pipeline = V3Pipeline("unused.db", dedup_hours=24, risk_config=RiskConfig(strategy_id="test_strategy", max_open_orders=5))
    return pipeline


def test_run_uses_constructor_defaults_when_no_override():
    pipeline = _build_pipeline()
    pipeline._risk.check = MagicMock(return_value=MagicMock(allowed=True))
    pipeline._dedup.check = MagicMock(return_value=MagicMock(is_dup=False))
    pipeline._candidate_repo.generate_signal_id = MagicMock(return_value="SIG-1")
    pipeline._candidate_repo.save = MagicMock(return_value=MagicMock(signal_id="SIG-1", symbol="BTCUSDT", direction="LONG"))
    pipeline._push_repo.already_queued = MagicMock(return_value=False)

    result = pipeline.run(_FakeStrategy([_FakeCandidateInput()]), now=datetime.now(UTC))

    assert result.pushed == 1
    _, kwargs = pipeline._dedup.check.call_args
    assert kwargs["window_hours"] == 24
    _, risk_kwargs = pipeline._risk.check.call_args
    assert risk_kwargs["config"].max_open_orders == 5


def test_run_honors_live_override_without_mutating_pipeline():
    pipeline = _build_pipeline()
    pipeline._risk.check = MagicMock(return_value=MagicMock(allowed=True))
    pipeline._dedup.check = MagicMock(return_value=MagicMock(is_dup=False))
    pipeline._candidate_repo.generate_signal_id = MagicMock(return_value="SIG-1")
    pipeline._candidate_repo.save = MagicMock(return_value=MagicMock(signal_id="SIG-1", symbol="BTCUSDT", direction="LONG"))
    pipeline._push_repo.already_queued = MagicMock(return_value=False)

    override_cfg = RiskConfig(strategy_id="test_strategy", max_open_orders=20)
    result = pipeline.run(
        _FakeStrategy([_FakeCandidateInput()]),
        now=datetime.now(UTC),
        dedup_hours=12,
        risk_config=override_cfg,
    )

    assert result.pushed == 1
    _, kwargs = pipeline._dedup.check.call_args
    assert kwargs["window_hours"] == 12
    _, risk_kwargs = pipeline._risk.check.call_args
    assert risk_kwargs["config"] is override_cfg

    # constructor-time defaults on the pipeline instance itself are untouched
    assert pipeline._dedup_hours == 24
    assert pipeline._risk_config.max_open_orders == 5
