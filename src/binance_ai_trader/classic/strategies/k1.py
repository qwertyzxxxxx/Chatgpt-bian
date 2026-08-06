"""K1 Pullback Long — 回踩多

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

STRATEGY_ID   = "classic_k1"
STRATEGY_NAME = "K1 回踩多"

# 策略篩選條件常量索引（/conditions 命令從此讀取，勿手寫說明文字）
CONDITIONS = {
    "strategy_id":      STRATEGY_ID,
    "strategy_version": "c1_v1",
    "direction":        "LONG",
    "timeframes":       "4H / 1H / 15m（1D 未使用）",
    "pool":             f"Top {CFG.universe_pool_size} 漲幅 + Top {CFG.universe_pool_size} 跌幅，成交額 ≥ {int(CFG.min_quote_volume_24h/Decimal('1000000'))}M USDT",
    "min_quote_volume": CFG.min_quote_volume_24h,
    "min_move_pct":     "未使用（通過 pool_size 間接篩選動量幣）",
    "d1":               "未使用",
    "h4":               (
        f"EMA20 > EMA60 且 EMA20 上升 且 price > EMA20；"
        f"price 距 EMA20 ≤ {CFG.c1_4h_ema_dist_max_atr} ATR4H；"
        f"屏蔽條件（滿足 2/3 跳過）：7d漲幅>{CFG.c1_block_7d_gain}% / "
        f"30d位置>{int(CFG.c1_block_30d_pos*100)}% / 距EMA>{CFG.c1_block_ema_dist} ATR"
    ),
    "h1":               f"前段放量上漲（量比≥{CFG.c1_rally_vol_min}）+ 縮量回踩（量比≤{CFG.c1_pullback_vol_max}）+ 出現更高低點 HL",
    "m15":              f"收盤 > EMA20_15m；量比 ≥ {CFG.c1_restart_vol_min}",
    "ema":              "4H EMA20/60（趨勢判斷）；15m EMA20（入場基準）",
    "rsi":              "未使用",
    "atr":              f"4H ATR14 → 距EMA距離判斷；15m ATR14 × {CFG.sl_atr_buffer} → 止損 buffer",
    "volume":           (
        f"1H 前浪量比 ≥ {CFG.c1_rally_vol_min}（動能確認）；"
        f"1H 回踩量比 ≤ {CFG.c1_pullback_vol_max}（縮量確認）；"
        f"15m 入場量比 ≥ {CFG.c1_restart_vol_min}"
    ),
    "structure":        "1H 更高低點 HL（上升結構確認）；15m 近 8 根結構低點止損",
    "entry_trigger":    f"15m 收盤 > EMA20_15m 且量比 ≥ {CFG.c1_restart_vol_min}（當前 K 線市價）",
    "sl_calc":          f"struct_low(近8根15m) − {CFG.sl_atr_buffer}×ATR14_15m；止損距離 ≤ {CFG.max_stop_pct}%",
    "tp_calc":          f"vol_A: TP1=×{CFG.tp1_r_a} TP2=×{CFG.tp2_r_a}；vol_S: TP1=×{CFG.tp1_r_s} TP2=×{CFG.tp2_r_s}；vol_S+: TP1=×{CFG.tp1_r_s_plus} TP2=×{CFG.tp2_r_s_plus}",
    "rr":               f"A級={CFG.tp1_r_a} / S級={CFG.tp1_r_s} / S+級={CFG.tp1_r_s_plus}",
    "timeout_hours":    CFG.hold_hours,
    "cooldown_hours":   CFG.dedup_hours,
    "dedup":            f"{CFG.dedup_hours}h 同幣同方向去重",
    "max_signals":      f"每策略 {CFG.max_per_strategy} 單/輪，全策略合計 ≤ {CFG.max_total} 單/輪",
    "enabled_env":      "ENABLE_CLASSIC=true",
    "score_threshold":  f"生成訂單 ≥ {CFG.score_signal_min}分；僅記錄 ≥ {CFG.score_watch_min}分",
}


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
        "cl": cl, "score_breakdown": sb,
    }, []
