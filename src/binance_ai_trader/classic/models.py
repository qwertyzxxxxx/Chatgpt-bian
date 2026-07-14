"""Data models for Classic C1-C4 strategies."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CoinContext:
    """All computed indicators for a single coin, shared across strategies."""
    symbol: str
    direction: str          # "LONG" or "SHORT"
    pool_type: str          # "TOP_GAINERS" or "TOP_LOSERS"
    pool_rank: int
    change_24h: Decimal
    quote_volume_24h: Decimal

    # Daily metrics
    change_3d: Decimal
    change_7d: Decimal
    range_pos_30d: Decimal
    consec_days: int
    consec_direction: str   # "UP" or "DOWN"

    # 4H indicators
    ema20_4h: Decimal
    ema60_4h: Decimal
    atr14_4h: Decimal
    ema20_4h_up: bool       # EMA20 trending up
    price_dist_4h_atr: Decimal  # abs(price - EMA20_4h) / ATR14_4h

    # 1H indicators
    ema20_1h: Decimal
    vol_ratio_1h: Decimal   # avg vol_ratio of recent segment
    has_higher_low_1h: bool
    has_lower_high_1h: bool

    # 15m indicators
    ema20_15m: Decimal
    atr14_15m: Decimal
    vol_ratio_15m: Decimal  # current bar vol_ratio
    vol_grade_15m: str      # WEAK/NORMAL/A/S/S_PLUS/EXHAUSTION

    # Current price
    current_price: Decimal


@dataclass
class ClassicSignal:
    strategy_id: str        # classic_c1 / classic_c2 / classic_c3 / classic_c4_top / classic_c4_bot
    strategy_name: str      # Chinese name
    symbol: str
    direction: str
    pool_type: str
    pool_rank: int

    entry: Decimal
    sl: Decimal
    tp1: Decimal
    tp2: Decimal
    rr: Decimal
    stop_pct: Decimal

    score: int
    vol_grade: str
    vol_ratio_1h: Decimal
    vol_ratio_15m: Decimal

    change_3d: Decimal
    change_7d: Decimal
    change_24h: Decimal
    range_pos_30d: Decimal
    consec_days: int
    dist_4h_ema_atr: Decimal

    pattern_desc: str
    block_checks: str       # summary of block condition checks
    rejection: str          # empty if signal produced


@dataclass
class ScanRecord:
    """One row saved to classic_scan_records for every evaluated coin."""
    scan_id: str
    strategy_id: str
    scanned_at: str
    symbol: str
    pool_type: str
    pool_rank: int
    direction: str
    change_24h: float
    quote_volume: float
    change_3d: float
    change_7d: float
    range_pos_30d: float
    consec_days: int
    trend_4h: str
    atr_dist_4h: float
    vol_ratio_1h: float
    vol_ratio_15m: float
    vol_grade: str
    price_pattern: str
    score: int
    passed: bool
    entry: str | None
    sl: str | None
    tp1: str | None
    tp2: str | None
    rr: str | None
    rejection: str
    signal_id: str | None
