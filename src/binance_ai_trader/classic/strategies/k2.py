"""K2 主升回踩多

Pool: Top 20 gainers (涨幅前20)
Logic: 4H上升趋势 → 前段放量上涨 → 缩量回踩 Higher Low → 重新放量站上EMA20做多
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_from_klines, has_higher_low,
    struct_low, vol_grade, vol_ratio, vol_ratio_for_segment,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID   = "classic_k2"
STRATEGY_NAME = "K2 主升回踩多"

CONDITIONS = {
    "strategy_id":  STRATEGY_ID,
    "direction":    "LONG",
    "pool":         "Top 20 涨幅，24h成交额 ≥ 3000万 USDT",
    "4h_trend":    "close > EMA20 > EMA60，EMA20向上，价格距EMA20 ≤ 1.5 ATR",
    "block":       "7日涨幅>40% / 30日位置>0.90 / 距EMA>2ATR 满足2项禁止",
    "1h_pattern":  "前段放量上涨（量比≥1.5）+ 缩量回踩（量比≤0.8）+ Higher Low",
    "15m_entry":   "价格站上EMA20，量比≥1.2",
    "sl":          "近8根15m结构低点 - 0.2×ATR14",
    "tp":          "A级2.0R / S级2.5R / S+级3.0R",
    "hold_hours":  48,
}


def evaluate(
    ctx: CoinContext,
    klines_15m: tuple[Kline, ...],
    klines_1h: tuple[Kline, ...],
    klines_4h: tuple[Kline, ...],
) -> tuple[dict | None, list[str]]:
    rejs: list[str] = []

    if ctx.direction != "LONG":
        return None, ["direction_not_long"]

    # ── 4H 趋势确认 ───────────────────────────────────────────────────────────
    if not (ctx.ema20_4h > ctx.ema60_4h):
        rejs.append("4h_ema20_not_above_ema60")
    if not ctx.ema20_4h_up:
        rejs.append("4h_ema20_not_trending_up")
    if ctx.current_price < ctx.ema20_4h:
        rejs.append("price_below_4h_ema20")
    if ctx.price_dist_4h_atr > Decimal("1.5"):
        rejs.append(f"4h_dist_too_far_{float(ctx.price_dist_4h_atr):.2f}>1.5ATR")

    if rejs:
        return None, rejs

    # ── 禁止追高（满足2/3即拦截）────────────────────────────────────────────
    block_flags = 0
    block_parts: list[str] = []
    if ctx.change_7d > Decimal("40"):
        block_flags += 1
        block_parts.append(f"7d+{float(ctx.change_7d):.1f}%>40%")
    if ctx.range_pos_30d > Decimal("0.90"):
        block_flags += 1
        block_parts.append(f"30d_pos={float(ctx.range_pos_30d):.2f}>0.90")
    if ctx.price_dist_4h_atr > Decimal("2.0"):
        block_flags += 1
        block_parts.append(f"dist={float(ctx.price_dist_4h_atr):.2f}ATR>2.0")
    block_summary = "; ".join(block_parts) if block_parts else "OK"

    if block_flags >= 2:
        return None, [f"BLOCKED_CHASE_HIGH(2/3): {block_summary}"]

    # ── 1H: 前段放量上涨 + 缩量回踩 + Higher Low ──────────────────────────────
    if len(klines_1h) < 20:
        return None, ["not_enough_1h_klines"]

    split      = len(klines_1h) // 2
    rally_seg  = klines_1h[:split]
    pb_seg     = klines_1h[split:]

    vr_rally   = vol_ratio_for_segment(rally_seg, baseline=klines_1h)
    vr_pb_1h   = vol_ratio_for_segment(pb_seg,    baseline=klines_1h)

    if vr_rally < Decimal("1.5"):
        rejs.append(f"rally_vol_low_{float(vr_rally):.2f}<1.5")
    if vr_pb_1h > Decimal("0.8"):
        rejs.append(f"pullback_vol_high_{float(vr_pb_1h):.2f}>0.8")
    if not has_higher_low(klines_1h):
        rejs.append("no_higher_low_1h")

    # ── 15m 入场 ──────────────────────────────────────────────────────────────
    if len(klines_15m) < 22:
        return None, ["not_enough_15m_klines"]

    ema20_15m = ema_from_klines(klines_15m, 20)
    atr14_15m = atr(klines_15m, 14)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]

    if cur_k.close < ema20_15m:
        rejs.append("price_below_15m_ema20")
    if vr_15m < Decimal("1.2"):
        rejs.append(f"restart_vol_low_{float(vr_15m):.2f}<1.2")

    if rejs:
        return None, rejs

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    entry    = cur_k.close
    sl_raw   = struct_low(klines_15m, lookback=8) - CFG.sl_atr_buffer * atr14_15m
    risk     = entry - sl_raw
    if risk <= 0:
        return None, ["invalid_risk_negative"]

    stop_pct = risk / entry * 100
    if stop_pct > CFG.max_stop_pct:
        return None, [f"stop_pct_too_wide_{float(stop_pct):.2f}%>{CFG.max_stop_pct}%"]

    grade  = vol_grade(vr_15m)
    tp1_r  = CFG.tp1_r_s if grade in ("S", "S_PLUS") else CFG.tp1_r_a
    tp2_r  = CFG.tp2_r_s if grade in ("S", "S_PLUS") else CFG.tp2_r_a
    tp1    = entry + risk * tp1_r
    tp2    = entry + risk * tp2_r
    rr     = tp1_r

    sb = compute_score(
        ctx, STRATEGY_ID,
        pattern_complete=(vr_rally >= Decimal("1.5")),
        pullback_quality=(vr_pb_1h <= Decimal("0.8") and ctx.has_higher_low_1h),
        entry_trigger=(cur_k.close >= ema20_15m and vr_15m >= Decimal("1.2")),
        vr_impulse=vr_rally,
        vr_pullback=vr_pb_1h,
        cl=close_location(cur_k),
    )

    pattern_desc = (
        f"K2主升回踩：4H趋势多头；1H放量上涨{float(vr_rally):.2f}x缩量回踩{float(vr_pb_1h):.2f}x；"
        f"15m站上EMA20量比{float(vr_15m):.2f}x；30d位置{float(ctx.range_pos_30d):.2f}"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": block_summary,
        "vr_impulse": vr_rally, "vr_pullback": vr_pb_1h, "cl": close_location(cur_k),
        "score_breakdown": sb,
    }, []
