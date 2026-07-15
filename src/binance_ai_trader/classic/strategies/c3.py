"""C3 Rally Short — 反弹空

Pool: Top 20 losers
Logic: Downtrend → prior impulse decline (vol ≥ 1.5x) → weak rally (vol ≤ 0.8x) → Lower High → re-entry short (vol ≥ 1.2x)
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_direction_up, ema_from_klines, has_lower_high,
    struct_high, vol_grade, vol_ratio, vol_ratio_for_segment,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID   = "classic_c3"
STRATEGY_NAME = "C3 反弹空"


def evaluate(
    ctx: CoinContext,
    klines_15m: tuple[Kline, ...],
    klines_1h: tuple[Kline, ...],
    klines_4h: tuple[Kline, ...],
) -> tuple[dict | None, list[str]]:
    rejs: list[str] = []
    if ctx.direction != "SHORT":
        return None, ["direction_not_short"]

    # ── 4H trend conditions ───────────────────────────────────────────────────
    if not (ctx.ema20_4h < ctx.ema60_4h):
        rejs.append("4h_ema20_not_below_ema60")
    if ctx.ema20_4h_up:
        rejs.append("4h_ema20_not_trending_down")
    if ctx.current_price >= ctx.ema20_4h:
        rejs.append("price_above_4h_ema20")

    # ── Block checks ──────────────────────────────────────────────────────────
    block_flags = 0
    block_parts = []
    fall_7d = abs(ctx.change_7d) if ctx.change_7d < 0 else Decimal("0")
    if fall_7d > CFG.c3_block_7d_fall:
        block_flags += 1
        block_parts.append(f"7d-{float(fall_7d):.1f}%>{CFG.c3_block_7d_fall}%")
    if ctx.range_pos_30d < CFG.c3_block_30d_pos:
        block_flags += 1
        block_parts.append(f"30d_pos={float(ctx.range_pos_30d):.2f}<{CFG.c3_block_30d_pos}")
    if ctx.price_dist_4h_atr > CFG.c3_block_ema_dist:
        block_flags += 1
        block_parts.append(f"dist={float(ctx.price_dist_4h_atr):.2f}ATR>{CFG.c3_block_ema_dist}ATR")
    block_checks = "; ".join(block_parts) if block_parts else "OK"

    if block_flags >= 2:
        rejs.append(f"BLOCKED(2/3): {block_checks}")

    if rejs:
        return None, rejs

    # ── 1H pattern: prior decline + weak rally + Lower High ───────────────────
    if len(klines_1h) < 20:
        return None, ["not_enough_1h_klines"]

    split       = len(klines_1h) // 2
    decline_seg = klines_1h[:split]
    rally_seg   = klines_1h[split:]

    vr_decline = vol_ratio_for_segment(decline_seg, baseline=klines_1h)
    vr_rally   = vol_ratio_for_segment(rally_seg,   baseline=klines_1h)

    if vr_decline < CFG.c3_decline_vol_min:
        rejs.append(f"decline_vol_low_{float(vr_decline):.2f}<{CFG.c3_decline_vol_min}")
    if vr_rally > CFG.c3_rally_vol_max:
        rejs.append(f"rally_vol_high_{float(vr_rally):.2f}>{CFG.c3_rally_vol_max}")
    if not has_lower_high(klines_1h):
        rejs.append("no_lower_high_1h")

    # ── 15m re-entry short conditions ─────────────────────────────────────────
    ema20_15m = ema_from_klines(klines_15m, 20)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]
    cl        = close_location(cur_k)
    atr14_15m = atr(klines_15m, 14)

    below_ema = cur_k.close < ema20_15m
    if not below_ema:
        rejs.append("price_not_below_15m_ema20")
    if vr_15m < CFG.c3_restart_vol_min:
        rejs.append(f"restart_vol_low_{float(vr_15m):.2f}<{CFG.c3_restart_vol_min}")

    if rejs:
        return None, rejs

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    sl_raw   = struct_high(klines_15m, lookback=8) + CFG.sl_atr_buffer * atr14_15m
    entry    = cur_k.close
    risk     = sl_raw - entry
    if risk <= 0:
        return None, ["invalid_risk_negative"]

    stop_pct = risk / entry * 100
    if stop_pct > CFG.max_stop_pct:
        return None, [f"stop_pct_too_wide_{float(stop_pct):.2f}%>{CFG.max_stop_pct}%"]

    grade    = vol_grade(vr_15m)
    tp1_r    = CFG.tp1_r_s if grade in ("S", "S_PLUS") else CFG.tp1_r_a
    tp2_r    = CFG.tp2_r_s if grade in ("S", "S_PLUS") else CFG.tp2_r_a
    tp1      = entry - risk * tp1_r
    tp2      = entry - risk * tp2_r
    rr       = tp1_r

    sb = compute_score(
        ctx, STRATEGY_ID,
        pattern_complete=vr_decline >= CFG.c3_decline_vol_min,
        pullback_quality=vr_rally <= CFG.c3_rally_vol_max and ctx.has_lower_high_1h,
        entry_trigger=below_ema and vr_15m >= CFG.c3_restart_vol_min,
        vr_impulse=vr_decline,
        vr_pullback=vr_rally,
        cl=cl,
    )

    pattern_desc = (
        f"4H趋势空头排列；1H前段下跌量比{float(vr_decline):.2f}x反弹缩量{float(vr_rally):.2f}x；"
        f"Lower High确认；15m重新跌破EMA20量比{float(vr_15m):.2f}x"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": block_checks,
        "vr_impulse": vr_decline, "vr_pullback": vr_rally, "cl": cl,
    }, []
