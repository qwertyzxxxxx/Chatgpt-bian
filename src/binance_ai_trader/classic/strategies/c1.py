"""C1 Pullback Long — 回踩多

Pool: Top 20 gainers
Logic: Uptrend → prior impulse wave (vol ≥ 1.5x) → first pullback (vol ≤ 0.8x) → Higher Low → re-entry (vol ≥ 1.2x)
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_direction_up, ema_from_klines, has_higher_low,
    struct_low, vol_grade, vol_ratio, vol_ratio_for_segment,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID   = "classic_c1"
STRATEGY_NAME = "C1 回踩多"


def evaluate(
    ctx: CoinContext,
    klines_15m: tuple[Kline, ...],
    klines_1h: tuple[Kline, ...],
    klines_4h: tuple[Kline, ...],
) -> tuple[dict | None, list[str]]:
    """
    Returns (signal_dict, rejections).
    signal_dict has keys: entry, sl, tp1, tp2, rr, stop_pct, score, vol_grade, pattern_desc, block_checks
    Returns (None, rejections) if no signal.
    """
    rejs: list[str] = []
    if ctx.direction != "LONG":
        return None, ["direction_not_long"]

    # ── 4H trend conditions ───────────────────────────────────────────────────
    if not (ctx.ema20_4h > ctx.ema60_4h):
        rejs.append("4h_ema20_not_above_ema60")
    if not ctx.ema20_4h_up:
        rejs.append("4h_ema20_not_trending_up")
    if ctx.current_price <= ctx.ema20_4h:
        rejs.append("price_below_4h_ema20")
    if ctx.price_dist_4h_atr > CFG.c1_4h_ema_dist_max_atr:
        rejs.append(f"price_too_far_from_4h_ema20_{float(ctx.price_dist_4h_atr):.2f}ATR")

    # ── Block checks (if 2/3 met → block) ────────────────────────────────────
    block_flags = 0
    block_parts = []
    if ctx.change_7d > CFG.c1_block_7d_gain:
        block_flags += 1
        block_parts.append(f"7d+{float(ctx.change_7d):.1f}%>{CFG.c1_block_7d_gain}%")
    if ctx.range_pos_30d > CFG.c1_block_30d_pos:
        block_flags += 1
        block_parts.append(f"30d_pos={float(ctx.range_pos_30d):.2f}>{CFG.c1_block_30d_pos}")
    if ctx.price_dist_4h_atr > CFG.c1_block_ema_dist:
        block_flags += 1
        block_parts.append(f"dist={float(ctx.price_dist_4h_atr):.2f}ATR>{CFG.c1_block_ema_dist}ATR")
    block_checks = "; ".join(block_parts) if block_parts else "OK"

    if block_flags >= 2:
        rejs.append(f"BLOCKED(2/3): {block_checks}")

    if rejs:
        return None, rejs

    # ── 1H pattern: prior impulse + pullback + Higher Low ─────────────────────
    if len(klines_1h) < 20:
        return None, ["not_enough_1h_klines"]

    # Split 1H into two halves: older = impulse zone, recent = pullback zone
    split = len(klines_1h) // 2
    impulse_seg = klines_1h[:split]
    pullback_seg = klines_1h[split:]

    vr_impulse  = vol_ratio_for_segment(impulse_seg,  baseline=klines_1h)
    vr_pullback = vol_ratio_for_segment(pullback_seg, baseline=klines_1h)

    if vr_impulse < CFG.c1_rally_vol_min:
        rejs.append(f"impulse_vol_low_{float(vr_impulse):.2f}<{CFG.c1_rally_vol_min}")
    if vr_pullback > CFG.c1_pullback_vol_max:
        rejs.append(f"pullback_vol_high_{float(vr_pullback):.2f}>{CFG.c1_pullback_vol_max}")
    if not has_higher_low(klines_1h):
        rejs.append("no_higher_low_1h")

    # ── 15m re-entry conditions ───────────────────────────────────────────────
    ema20_15m = ema_from_klines(klines_15m, 20)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]
    cl        = close_location(cur_k)
    atr14_15m = atr(klines_15m, 14)

    above_ema = cur_k.close > ema20_15m
    if not above_ema:
        rejs.append("price_not_above_15m_ema20")
    if vr_15m < CFG.c1_restart_vol_min:
        rejs.append(f"restart_vol_low_{float(vr_15m):.2f}<{CFG.c1_restart_vol_min}")

    if rejs:
        return None, rejs

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    sl_raw    = struct_low(klines_15m, lookback=8) - CFG.sl_atr_buffer * atr14_15m
    entry     = cur_k.close
    risk      = entry - sl_raw
    if risk <= 0:
        return None, ["invalid_risk_negative"]

    stop_pct = risk / entry * 100
    if stop_pct > CFG.max_stop_pct:
        return None, [f"stop_pct_too_wide_{float(stop_pct):.2f}%>{CFG.max_stop_pct}%"]

    grade     = vol_grade(vr_15m)
    tp1_r     = CFG.tp1_r_s if grade in ("S", "S_PLUS") else CFG.tp1_r_a
    tp2_r     = CFG.tp2_r_s if grade in ("S", "S_PLUS") else CFG.tp2_r_a
    tp1       = entry + risk * tp1_r
    tp2       = entry + risk * tp2_r
    rr        = tp1_r

    # ── Score ──────────────────────────────────────────────────────────────────
    sb = compute_score(
        ctx, STRATEGY_ID,
        pattern_complete=vr_impulse >= CFG.c1_rally_vol_min,
        pullback_quality=vr_pullback <= CFG.c1_pullback_vol_max and ctx.has_higher_low_1h,
        entry_trigger=above_ema and vr_15m >= CFG.c1_restart_vol_min,
        vr_impulse=vr_impulse,
        vr_pullback=vr_pullback,
        cl=cl,
    )

    pattern_desc = (
        f"4H趋势多头排列；1H前段量比{float(vr_impulse):.2f}x回踩缩量{float(vr_pullback):.2f}x；"
        f"Higher Low确认；15m量比{float(vr_15m):.2f}x重启"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": block_checks,
        "vr_impulse": vr_impulse, "vr_pullback": vr_pullback,
        "cl": cl,
    }, []
