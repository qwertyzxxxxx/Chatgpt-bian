"""Classic C1-C4 strategy configuration — all thresholds in one place.

Edit this file to tune strategy parameters. No hardcoded values elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ClassicConfig:
    # ── Universe filter ───────────────────────────────────────────────────────
    min_quote_volume_24h: Decimal = Decimal("30_000_000")
    universe_pool_size: int = 20            # top N gainers + top N losers

    # ── Kline fetch limits ────────────────────────────────────────────────────
    limit_15m: int = 55                     # fetch 55, use [:-1] = 54 closed
    limit_1h: int = 55
    limit_4h: int = 65
    limit_1d: int = 32

    # ── Volume analysis ───────────────────────────────────────────────────────
    vol_ratio_lookback: int = 20            # median of previous N closed bars
    vol_grade_normal_min: Decimal = Decimal("1.2")
    vol_grade_a_min: Decimal = Decimal("1.5")
    vol_grade_s_min: Decimal = Decimal("2.0")
    vol_grade_s_plus_min: Decimal = Decimal("4.0")
    vol_grade_s_plus_max: Decimal = Decimal("8.0")
    vol_grade_exhaustion_min: Decimal = Decimal("3.0")

    # ── Stop-loss rules ───────────────────────────────────────────────────────
    max_stop_pct: Decimal = Decimal("6")    # reject if SL > 6% from entry
    sl_atr_buffer: Decimal = Decimal("0.2") # SL = structure ± 0.2 × ATR14

    # ── Scoring thresholds ────────────────────────────────────────────────────
    score_signal_min: int = 75              # generate paper order
    score_watch_min: int = 65              # log only, no order

    # ── Signal limits per cycle ───────────────────────────────────────────────
    max_per_strategy: int = 1
    max_total: int = 3

    # ── Dedup ─────────────────────────────────────────────────────────────────
    dedup_hours: int = 24

    # ── Hold time ─────────────────────────────────────────────────────────────
    hold_hours: int = 48

    # ── C1 Pullback Long ─────────────────────────────────────────────────────
    c1_4h_ema_dist_max_atr: Decimal = Decimal("1.5")  # price <= 1.5 ATR from 4H EMA20
    c1_block_7d_gain: Decimal = Decimal("40")
    c1_block_30d_pos: Decimal = Decimal("0.90")
    c1_block_ema_dist: Decimal = Decimal("2.0")
    c1_rally_vol_min: Decimal = Decimal("1.5")         # prior rally vol_ratio
    c1_pullback_vol_max: Decimal = Decimal("0.8")      # pullback vol_ratio
    c1_restart_vol_min: Decimal = Decimal("1.2")       # re-entry vol_ratio
    c1_close_location_bull: Decimal = Decimal("0.70")  # strong bullish close

    # ── C2 Breakout Long ─────────────────────────────────────────────────────
    c2_platform_bars: int = 20
    c2_platform_max_range_pct: Decimal = Decimal("8")  # platform width <= 8%
    c2_platform_tests_min: int = 2
    c2_block_7d_gain: Decimal = Decimal("30")
    c2_block_30d_pos: Decimal = Decimal("0.90")
    c2_block_ema_dist: Decimal = Decimal("1.0")
    c2_breakout_vol_min: Decimal = Decimal("2.0")
    c2_breakout_close_loc_min: Decimal = Decimal("0.70")
    c2_breakout_upper_wick_max: Decimal = Decimal("0.30")
    c2_pullback_vol_max: Decimal = Decimal("0.8")
    c2_restart_vol_min: Decimal = Decimal("1.2")
    c2_post_break_ema_dist_max: Decimal = Decimal("2.0")

    # ── C3 Rally Short ────────────────────────────────────────────────────────
    c3_block_7d_fall: Decimal = Decimal("35")
    c3_block_30d_pos: Decimal = Decimal("0.15")
    c3_block_ema_dist: Decimal = Decimal("2.0")
    c3_decline_vol_min: Decimal = Decimal("1.5")
    c3_rally_vol_max: Decimal = Decimal("0.8")
    c3_restart_vol_min: Decimal = Decimal("1.2")
    c3_close_location_bear: Decimal = Decimal("0.30")  # strong bearish close

    # ── C4 Extreme Reversal ───────────────────────────────────────────────────
    # C4_TOP_SHORT
    c4_top_consec_days_min: int = 3
    c4_top_7d_gain_min: Decimal = Decimal("30")
    c4_top_30d_pos_min: Decimal = Decimal("0.85")
    c4_top_ema_dist_min: Decimal = Decimal("1.5")
    c4_top_exhaust_vol_min: Decimal = Decimal("3.0")
    # C4_BOTTOM_LONG
    c4_bot_consec_days_min: int = 3
    c4_bot_7d_fall_min: Decimal = Decimal("25")
    c4_bot_30d_pos_max: Decimal = Decimal("0.15")
    c4_bot_ema_dist_min: Decimal = Decimal("1.5")
    c4_bot_panic_vol_min: Decimal = Decimal("3.0")

    # ── TP multiples by vol grade ─────────────────────────────────────────────
    tp1_r_normal: Decimal = Decimal("1.5")   # NORMAL (auxiliary)
    tp1_r_a: Decimal = Decimal("1.5")
    tp1_r_s: Decimal = Decimal("2.0")
    tp1_r_s_plus: Decimal = Decimal("3.0")
    tp2_r_normal: Decimal = Decimal("2.0")
    tp2_r_a: Decimal = Decimal("2.0")
    tp2_r_s: Decimal = Decimal("3.0")
    tp2_r_s_plus: Decimal = Decimal("5.0")


CFG = ClassicConfig()
