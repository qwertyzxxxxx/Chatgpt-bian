"""C2 Breakout Long — 突破多

Pool: Top 20 gainers
Logic: Platform → volume breakout (vol ≥ 2x) → pullback to platform (vol ≤ 0.8x) → Higher Low → re-entry (vol ≥ 1.2x)
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_from_klines, has_higher_low,
    platform_stats, struct_low, upper_wick_ratio, vol_grade, vol_ratio,
    vol_ratio_for_segment,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID   = "classic_c2"
STRATEGY_NAME = "C2 突破多"


def evaluate(
    ctx: CoinContext,
    klines_15m: tuple[Kline, ...],
    klines_1h: tuple[Kline, ...],
    klines_4h: tuple[Kline, ...],
) -> tuple[dict | None, list[str]]:
    rejs: list[str] = []
    if ctx.direction != "LONG":
        return None, ["direction_not_long"]

    # ── Block checks ──────────────────────────────────────────────────────────
    block_flags = 0
    block_parts = []
    if ctx.change_7d > CFG.c2_block_7d_gain:
        block_flags += 1
        block_parts.append(f"7d+{float(ctx.change_7d):.1f}%>{CFG.c2_block_7d_gain}%")
    if ctx.range_pos_30d > CFG.c2_block_30d_pos:
        block_flags += 1
        block_parts.append(f"30d_pos={float(ctx.range_pos_30d):.2f}>{CFG.c2_block_30d_pos}")
    if ctx.price_dist_4h_atr > CFG.c2_block_ema_dist:
        block_flags += 1
        block_parts.append(f"dist={float(ctx.price_dist_4h_atr):.2f}ATR>{CFG.c2_block_ema_dist}ATR")
    block_checks = "; ".join(block_parts) if block_parts else "OK"

    if block_flags >= 2:
        rejs.append(f"BLOCKED(2/3): {block_checks}")

    # ── 1H platform detection ─────────────────────────────────────────────────
    if len(klines_1h) < CFG.c2_platform_bars + 10:
        return None, ["not_enough_1h_klines"]

    pre_breakout_1h = klines_1h[-(CFG.c2_platform_bars + 10): -10]
    plat = platform_stats(pre_breakout_1h, CFG.c2_platform_bars)

    if not plat["is_tight"]:
        rejs.append(f"platform_too_wide_{plat['range_pct']:.1f}%>{CFG.c2_platform_max_range_pct}%")
    if plat["high_tests"] < CFG.c2_platform_tests_min:
        rejs.append(f"platform_high_tested_only_{plat['high_tests']}_times")
    if plat["low_tests"] < CFG.c2_platform_tests_min:
        rejs.append(f"platform_low_tested_only_{plat['low_tests']}_times")

    # ── 1H breakout candle (most recent prior breakout) ───────────────────────
    recent_10 = klines_1h[-10:]
    breakout_bar = max(recent_10, key=lambda k: k.quote_volume)
    vr_break     = vol_ratio(klines_1h[:-10] + (breakout_bar,))
    cl_break     = close_location(breakout_bar)
    uw_break     = upper_wick_ratio(breakout_bar)

    if breakout_bar.close <= plat["high"]:
        rejs.append("no_1h_breakout_above_platform")
    if vr_break < CFG.c2_breakout_vol_min:
        rejs.append(f"breakout_vol_low_{float(vr_break):.2f}<{CFG.c2_breakout_vol_min}")
    if cl_break < CFG.c2_breakout_close_loc_min:
        rejs.append(f"breakout_close_loc_weak_{float(cl_break):.2f}<{CFG.c2_breakout_close_loc_min}")
    if uw_break > CFG.c2_breakout_upper_wick_max:
        rejs.append(f"breakout_upper_wick_long_{float(uw_break):.2f}>{CFG.c2_breakout_upper_wick_max}")
    if ctx.price_dist_4h_atr > CFG.c2_post_break_ema_dist_max:
        rejs.append(f"post_break_too_far_from_ema20_{float(ctx.price_dist_4h_atr):.2f}ATR")

    # ── 15m pullback conditions ───────────────────────────────────────────────
    vr_pullback  = vol_ratio_for_segment(klines_1h[-8:])
    has_hl       = has_higher_low(klines_1h[-15:])
    ema20_15m    = ema_from_klines(klines_15m, 20)
    vr_15m       = vol_ratio(klines_15m)
    cur_k        = klines_15m[-1]
    cl            = close_location(cur_k)
    atr14_15m    = atr(klines_15m, 14)

    above_platform = cur_k.close >= plat["high"] * Decimal("0.98")  # allow 2% tolerance
    above_ema      = cur_k.close >= ema20_15m

    if not (above_platform or above_ema):
        rejs.append("price_not_above_platform_or_ema20_after_pullback")
    if vr_pullback > CFG.c2_pullback_vol_max:
        rejs.append(f"pullback_vol_high_{float(vr_pullback):.2f}>{CFG.c2_pullback_vol_max}")
    if not has_hl:
        rejs.append("no_higher_low_after_breakout")
    if vr_15m < CFG.c2_restart_vol_min:
        rejs.append(f"restart_vol_low_{float(vr_15m):.2f}<{CFG.c2_restart_vol_min}")

    if rejs:
        return None, rejs

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    sl_raw   = struct_low(klines_15m, lookback=8) - CFG.sl_atr_buffer * atr14_15m
    entry    = cur_k.close
    risk     = entry - sl_raw
    if risk <= 0:
        return None, ["invalid_risk_negative"]

    stop_pct = risk / entry * 100
    if stop_pct > CFG.max_stop_pct:
        return None, [f"stop_pct_too_wide_{float(stop_pct):.2f}%>{CFG.max_stop_pct}%"]

    grade    = vol_grade(vr_15m)
    tp1_r    = CFG.tp1_r_s if grade in ("S", "S_PLUS") else CFG.tp1_r_a
    tp2_r    = CFG.tp2_r_s if grade in ("S", "S_PLUS") else CFG.tp2_r_a
    tp1      = entry + risk * tp1_r
    tp2      = entry + risk * tp2_r
    rr       = tp1_r

    sb = compute_score(
        ctx, STRATEGY_ID,
        pattern_complete=vr_break >= CFG.c2_breakout_vol_min and plat["is_tight"],
        pullback_quality=vr_pullback <= CFG.c2_pullback_vol_max and has_hl,
        entry_trigger=above_platform or above_ema,
        vr_impulse=vr_break,
        vr_pullback=vr_pullback,
        cl=cl,
    )

    pattern_desc = (
        f"1H平台{plat['range_pct']:.1f}%宽(高测{plat['high_tests']}次/低测{plat['low_tests']}次)；"
        f"突破量比{float(vr_break):.2f}x；回踩缩量{float(vr_pullback):.2f}x；"
        f"15m重启{float(vr_15m):.2f}x"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": block_checks,
        "vr_impulse": vr_break, "vr_pullback": vr_pullback, "cl": cl,
    }, []
