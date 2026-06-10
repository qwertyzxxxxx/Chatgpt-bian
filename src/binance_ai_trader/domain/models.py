from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Contract:
    symbol: str
    base_asset: str
    quote_asset: str
    margin_asset: str
    contract_type: str
    status: str
    price_precision: int
    quantity_precision: int
    tick_size: Decimal
    step_size: Decimal


@dataclass(frozen=True, slots=True)
class Ticker24h:
    symbol: str
    quote_volume: Decimal
    price_change_percent: Decimal
    close_time_ms: int


@dataclass(frozen=True, slots=True)
class UniverseMember:
    contract: Contract
    ticker: Ticker24h

    @property
    def symbol(self) -> str:
        return self.contract.symbol


@dataclass(frozen=True, slots=True)
class Kline:
    symbol: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int


@dataclass(frozen=True, slots=True)
class CollectionResult:
    run_id: str
    universe: tuple[UniverseMember, ...]
    kline_count: int
    failed_requests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SymbolScore:
    symbol: str
    score: float
    score_breakdown: dict[str, object]
    algorithm_version: str


@dataclass(frozen=True, slots=True)
class ScoringResult:
    run_id: str
    ranked_scores: tuple[SymbolScore, ...]
    skipped_symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RankedScore:
    run_id: str
    rank: int
    score: SymbolScore
    tick_size: Decimal


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    snapshot_id: str
    snapshot_type: str
    collection_run_id: str | None
    source_ref: str
    data_cutoff_ms: int
    strategy_id: str
    created_at: str
    finalized_at: str | None


@dataclass(frozen=True, slots=True)
class TradeSignal:
    symbol: str
    direction: str
    score: float
    entry: Decimal
    latest_close: Decimal
    stop_loss: Decimal
    stop_loss_pct: Decimal
    tp1: Decimal
    tp2: Decimal
    rr_tp1: Decimal
    rr_tp2: Decimal
    logic_summary: str
    combined_regime: str = "OBSERVE"
    sector: str = "OTHER"
    sector_rank: int | None = None
    capital_score: float = 50.0
    space_score: float = 50.0
    final_signal_score: float = 50.0


@dataclass(frozen=True, slots=True)
class SignalResult:
    run_id: str | None
    signals: tuple[TradeSignal, ...]
    processed_symbols: int
    snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class StoredSignal:
    run_id: str
    symbol: str
    direction: str
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    generated_at: str
    generated_at_ms: int
    snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class SignalEvaluation:
    signal_run_id: str
    symbol: str
    direction: str
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    result: str
    max_favorable_pct: Decimal
    max_adverse_pct: Decimal
    bars_to_result: int
    snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    total_signals: int
    win_tp2_count: int
    tp1_hit_count: int
    loss_count: int
    expired_count: int
    tp1_hit_rate: float
    tp2_win_rate: float
    loss_rate: float
    expired_rate: float
    expectancy_r: float
    average_max_favorable_pct: float
    average_max_adverse_pct: float


@dataclass(frozen=True, slots=True)
class EvaluationSummary(EvaluationMetrics):
    by_direction: dict[str, EvaluationMetrics]


@dataclass(frozen=True, slots=True)
class MarketRegime:
    btc_regime: str
    eth_regime: str
    combined_regime: str


@dataclass(frozen=True, slots=True)
class SectorMember:
    symbol: str
    score: float
    change_24h: Decimal
    quote_volume_24h: Decimal


@dataclass(frozen=True, slots=True)
class SectorSnapshot:
    run_id: str
    sector: str
    sector_rank: int
    member_count: int
    avg_score: Decimal
    median_score: Decimal
    top3_avg_score: Decimal
    positive_24h_ratio: Decimal
    quote_volume_24h: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    evaluation_time_ms: int
    symbol: str
    direction: str
    combined_regime: str
    sector: str
    sector_rank: int | None
    score: float
    entry: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    rr_tp1: Decimal
    rr_tp2: Decimal
    result: str
    bars_to_result: int
    realized_r: Decimal
    capital_score: float = 50.0
    space_score: float = 50.0
    final_signal_score: float = 50.0
    snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    total_signals: int
    tp1_hit_rate: float
    tp2_win_rate: float
    loss_rate: float
    expired_rate: float
    profit_factor: float | None
    expectancy_r: float
    max_drawdown_r: float
    avg_rr_tp2: float


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    run_id: str
    started_at: str
    completed_at: str
    evaluation_points: int
    metrics: BacktestMetrics
    by_direction: dict[str, BacktestMetrics]
    by_combined_regime: dict[str, BacktestMetrics]
    by_sector: dict[str, BacktestMetrics]
    by_score_bucket: dict[str, BacktestMetrics]
    by_capital_bucket: dict[str, BacktestMetrics]
    by_space_bucket: dict[str, BacktestMetrics]
