"""Classic 100-point scoring system.

Categories:
  Time & Space Position  — 30 pts
  4H & 1H Trend          — 25 pts
  Price Pattern          — 25 pts
  Volume Confirmation    — 20 pts
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.models import CoinContext


@dataclass
class ScoreBreakdown:
    time_space: int     # max 30
    trend: int          # max 25
    pattern: int        # max 25
    volume: int         # max 20
    total: int


def score_time_space(ctx: CoinContext, strategy_id: str) -> int:
    """30 pts: position in 30d range, 7d change magnitude, consecutive days."""
    pts = 0
    rp = ctx.range_pos_30d
    ch7 = abs(ctx.change_7d)
    consec = ctx.consec_days

    # 30d range position (10 pts)
    if "c1" in strategy_id or "c2" in strategy_id or "k2" in strategy_id:
        # Long from trend: ideal if not at extreme top
        if Decimal("0.40") <= rp <= Decimal("0.80"):
            pts += 10
        elif Decimal("0.30") <= rp <= Decimal("0.90"):
            pts += 6
        else:
            pts += 2
    elif "k1" in strategy_id:
        # K1 bottom launch: ideal 0.15-0.65
        if Decimal("0.20") <= rp <= Decimal("0.55"):
            pts += 10
        elif Decimal("0.15") <= rp <= Decimal("0.65"):
            pts += 6
        else:
            pts += 0
    elif "c3" in strategy_id and "k3v2" not in strategy_id:
        # Short: ideal if not at extreme bottom
        if Decimal("0.20") <= rp <= Decimal("0.60"):
            pts += 10
        elif Decimal("0.15") <= rp <= Decimal("0.70"):
            pts += 6
        else:
            pts += 2
    elif "c4_top" in strategy_id or "k3" in strategy_id:
        # Top short/K3 exhaustion: high position is a plus
        if rp >= Decimal("0.85"):
            pts += 10
        elif rp >= Decimal("0.75"):
            pts += 6
        else:
            pts += 0
    elif "k3v2" in strategy_id:
        # K3v2 expanded: 0.80 minimum, so 0.90+ is best
        if rp >= Decimal("0.90"):
            pts += 10
        elif rp >= Decimal("0.80"):
            pts += 6
        else:
            pts += 0
    elif "k4v2" in strategy_id:
        # K4v2 expanded: 0.20 maximum, so 0.10- is best
        if rp <= Decimal("0.10"):
            pts += 10
        elif rp <= Decimal("0.20"):
            pts += 6
        else:
            pts += 0
    elif "c4_bot" in strategy_id or "k4" in strategy_id:
        # Bottom long/K4 panic: low position is a plus
        if rp <= Decimal("0.15"):
            pts += 10
        elif rp <= Decimal("0.25"):
            pts += 6
        else:
            pts += 0

    # 7d change magnitude (10 pts)
    if "c4_top" in strategy_id or "k3" in strategy_id or "k3v2" in strategy_id:
        pts += 10 if ch7 >= Decimal("40") else (7 if ch7 >= Decimal("30") else 3)
    elif "c4_bot" in strategy_id or "k4" in strategy_id or "k4v2" in strategy_id:
        pts += 10 if ch7 >= Decimal("35") else (7 if ch7 >= Decimal("25") else 3)
    elif "k1" in strategy_id:
        # K1: moderate gain is ideal (breakout just started)
        if Decimal("5") <= ch7 <= Decimal("25"):
            pts += 10
        elif Decimal("2") <= ch7 < Decimal("5") or Decimal("25") < ch7 <= Decimal("35"):
            pts += 6
        else:
            pts += 2
    else:
        # C1/C2/K2: moderate gain is better
        if Decimal("10") <= ch7 <= Decimal("35"):
            pts += 10
        elif Decimal("5") <= ch7 < Decimal("10") or Decimal("35") < ch7 <= Decimal("50"):
            pts += 6
        else:
            pts += 2

    # Consecutive trend days (10 pts)
    if "c4_top" in strategy_id or "c4_bot" in strategy_id or "k3" in strategy_id or "k4" in strategy_id or "k3v2" in strategy_id or "k4v2" in strategy_id:
        # More consecutive days = stronger signal for reversal
        pts += min(10, consec * 3)
    elif "c1" in strategy_id or "c2" in strategy_id or "k1" in strategy_id or "k2" in strategy_id:
        # 2-5 days trending up is good setup
        pts += 10 if 2 <= consec <= 6 else (5 if consec > 6 else 2)
    elif "c3" in strategy_id:
        pts += 10 if 2 <= consec <= 6 else (5 if consec > 6 else 2)

    return min(pts, 30)


def score_trend(ctx: CoinContext, strategy_id: str) -> int:
    """25 pts: 4H EMA alignment, EMA direction, 1H structure."""
    pts = 0

    # 4H EMA alignment (10 pts)
    if "c1" in strategy_id or "c2" in strategy_id or "k2" in strategy_id:
        if ctx.ema20_4h > ctx.ema60_4h and ctx.current_price > ctx.ema20_4h:
            pts += 10
        elif ctx.current_price > ctx.ema20_4h:
            pts += 5
    elif "k1" in strategy_id:
        # K1: 4H not too far from EMA20
        if ctx.price_dist_4h_atr <= Decimal("2.0") and ctx.current_price > ctx.ema20_4h:
            pts += 8
        elif ctx.price_dist_4h_atr <= Decimal("2.0"):
            pts += 4
    elif "c3" in strategy_id:
        if ctx.ema20_4h < ctx.ema60_4h and ctx.current_price < ctx.ema20_4h:
            pts += 10
        elif ctx.current_price < ctx.ema20_4h:
            pts += 5
    elif "c4_top" in strategy_id or "k3" in strategy_id:
        # Price far above EMA20 is a plus for reversal
        if ctx.price_dist_4h_atr >= CFG.c4_top_ema_dist_min:
            pts += 10
        elif ctx.price_dist_4h_atr >= Decimal("1.0"):
            pts += 5
    elif "c4_bot" in strategy_id or "k4" in strategy_id:
        if ctx.price_dist_4h_atr >= CFG.c4_bot_ema_dist_min:
            pts += 10
        elif ctx.price_dist_4h_atr >= Decimal("1.0"):
            pts += 5

    # 4H EMA direction (5 pts)
    if "c1" in strategy_id or "c2" in strategy_id or "k1" in strategy_id or "k2" in strategy_id:
        pts += 5 if ctx.ema20_4h_up else 0
    elif "c3" in strategy_id or "k3" in strategy_id:
        pts += 5 if not ctx.ema20_4h_up else 0
    else:
        pts += 3  # C4/K4/K3v2/K4v2: direction of EMA less critical

    # 1H structure (10 pts)
    if "c1" in strategy_id or "c2" in strategy_id or "c4_bot" in strategy_id or "k1" in strategy_id or "k2" in strategy_id or "k4" in strategy_id:
        pts += 10 if ctx.has_higher_low_1h else 3
    elif "c3" in strategy_id or "c4_top" in strategy_id or "k3" in strategy_id:
        pts += 10 if ctx.has_lower_high_1h else 3

    return min(pts, 25)


def score_pattern(
    pattern_complete: bool,
    pullback_quality: bool,
    entry_trigger: bool,
    strategy_id: str,
) -> int:
    """25 pts: pattern completeness, pullback quality, entry trigger."""
    pts = 0
    pts += 12 if pattern_complete else 0
    pts += 8 if pullback_quality else 0
    pts += 5 if entry_trigger else 0
    return min(pts, 25)


def score_volume(
    vr_15m: Decimal,
    vr_impulse: Decimal,
    vr_pullback: Decimal,
    cl: Decimal,
    direction: str,
) -> int:
    """20 pts: vol grade, close location, volume contraction."""
    pts = 0

    # Vol grade of restart bar (10 pts)
    if vr_15m >= CFG.vol_grade_s_plus_min:
        pts += 10
    elif vr_15m >= CFG.vol_grade_s_min:
        pts += 8
    elif vr_15m >= CFG.vol_grade_a_min:
        pts += 6
    elif vr_15m >= CFG.vol_grade_normal_min:
        pts += 3

    # Close location confirms direction (5 pts)
    if direction == "LONG" and cl >= CFG.c1_close_location_bull:
        pts += 5
    elif direction == "SHORT" and cl <= CFG.c3_close_location_bear:
        pts += 5
    elif Decimal("0.40") <= cl <= Decimal("0.60"):
        pts += 2

    # Volume contraction during pullback/rally (5 pts)
    if vr_pullback <= CFG.c1_pullback_vol_max:
        pts += 5
    elif vr_pullback <= Decimal("1.0"):
        pts += 2

    return min(pts, 20)


def compute_score(
    ctx: CoinContext,
    strategy_id: str,
    pattern_complete: bool,
    pullback_quality: bool,
    entry_trigger: bool,
    vr_impulse: Decimal,
    vr_pullback: Decimal,
    cl: Decimal,
) -> ScoreBreakdown:
    ts = score_time_space(ctx, strategy_id)
    tr = score_trend(ctx, strategy_id)
    pa = score_pattern(pattern_complete, pullback_quality, entry_trigger, strategy_id)
    vo = score_volume(ctx.vol_ratio_15m, vr_impulse, vr_pullback, cl, ctx.direction)
    return ScoreBreakdown(
        time_space=ts, trend=tr, pattern=pa, volume=vo, total=ts + tr + pa + vo
    )
