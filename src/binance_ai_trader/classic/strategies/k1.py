"""K1 底部启动多

Pool: Top 20 gainers (涨幅前20)
Logic: 长期下跌或低位整理 → 底部放量突破平台 → 缩量回踩 → Higher Low → 重新放量站上 EMA20 做多
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_from_klines, has_higher_low,
    struct_low, vol_grade, vol_ratio, vol_ratio_for_segment,
    upper_wick_ratio,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID   = "classic_k1"
STRATEGY_NAME = "K1 底部启动多"

CONDITIONS = {
    "strategy_id":   STRATEGY_ID,
    "direction":     "LONG",
    "pool":          "Top 20 涨幅，24h成交额 ≥ 3000万 USDT",
    "space":         "30日区间位置 0.15~0.65，7日涨幅 ≤ 35%，4H EMA距离 ≤ 2 ATR",
    "1h_pattern":   "近5根1H K线中有收盘突破平台高点（量比≥2.0，收盘位置≥0.70）",
    "15m_entry":    "价格拉回至EMA20附近，缩量回踩后重新放量站上EMA20（量比≥1.2），形成Higher Low",
    "sl":           "近8根15m结构低点 - 0.2×ATR14",
    "tp":           "A级2.0R / S级2.5R / S+级3.0R（以TP2为目标）",
    "hold_hours":   48,
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

    # ── 空间条件 ──────────────────────────────────────────────────────────────
    if ctx.range_pos_30d > Decimal("0.65"):
        rejs.append(f"range_pos_too_high_{float(ctx.range_pos_30d):.2f}>0.65")
    if ctx.range_pos_30d < Decimal("0.15"):
        rejs.append(f"range_pos_too_low_{float(ctx.range_pos_30d):.2f}<0.15")
    if ctx.change_7d > Decimal("35"):
        rejs.append(f"7d_gain_already_high_{float(ctx.change_7d):.1f}%>35%")
    # 4H未过度延伸
    if ctx.price_dist_4h_atr > Decimal("2.0"):
        rejs.append(f"4h_overextended_{float(ctx.price_dist_4h_atr):.2f}>2ATR")

    if rejs:
        return None, rejs

    # ── 1H: 检测近期平台突破 ──────────────────────────────────────────────────
    if len(klines_1h) < 22:
        return None, ["not_enough_1h_klines"]

    # 平台区间：最近24根中较早的12根（给出足够的平台确认）
    platform_bars = klines_1h[-24:-12]
    platform_high = max(k.high for k in platform_bars)
    platform_low  = min(k.low  for k in platform_bars)

    # 最近12根1H中是否有突破K（12小时内，覆盖半天内的突破）
    recent_1h = klines_1h[-12:]
    breakout_candidates = [
        k for k in recent_1h
        if k.close > platform_high
    ]
    if not breakout_candidates:
        return None, ["no_1h_platform_breakout"]

    # 取成交量最大的突破K评估质量
    breakout_k = max(breakout_candidates, key=lambda k: float(k.quote_volume))
    bl_vr = vol_ratio_for_segment((breakout_k,), baseline=klines_1h)
    bl_cl = close_location(breakout_k)
    bl_uw = upper_wick_ratio(breakout_k)

    if bl_vr < Decimal("2.0"):
        rejs.append(f"breakout_vol_low_{float(bl_vr):.2f}<2.0")
    if bl_cl < Decimal("0.70"):
        rejs.append(f"breakout_close_loc_weak_{float(bl_cl):.2f}<0.70")
    # 长上影且收回 → 假突破
    if bl_uw > Decimal("0.40") and bl_cl < Decimal("0.50"):
        rejs.append(f"fake_breakout_long_upper_wick_{float(bl_uw):.2f}")

    if rejs:
        return None, rejs

    # ── 15m: 回踩缩量 → 重新站上EMA20放量 ─────────────────────────────────────
    if len(klines_15m) < 22:
        return None, ["not_enough_15m_klines"]

    ema20_15m = ema_from_klines(klines_15m, 20)
    atr14_15m = atr(klines_15m, 14)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]

    # 当前K线站上EMA20（已经进入回踩后重启阶段）
    if cur_k.close < ema20_15m:
        rejs.append("price_below_15m_ema20")

    # 再启动量能
    if vr_15m < Decimal("1.2"):
        rejs.append(f"restart_vol_low_{float(vr_15m):.2f}<1.2")

    # 近期15m有缩量回踩（倒数2-9根的量比低于1.2）
    pullback_seg = klines_15m[-9:-2]
    vr_pb = vol_ratio_for_segment(pullback_seg, baseline=klines_15m)
    if vr_pb > Decimal("1.3"):
        rejs.append(f"no_volume_contraction_{float(vr_pb):.2f}>1.3")

    # 价格没有跌回平台深处（关闭价 > 平台低点）
    if cur_k.close < platform_low:
        rejs.append("price_fell_below_platform_low")

    # 1H Higher Low（回踩没破坏结构）
    if not has_higher_low(klines_1h):
        rejs.append("no_higher_low_1h")

    if rejs:
        return None, rejs

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    entry   = cur_k.close
    sl_raw  = struct_low(klines_15m, lookback=8) - CFG.sl_atr_buffer * atr14_15m
    risk    = entry - sl_raw
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
        pattern_complete=(bl_vr >= Decimal("2.0") and bl_cl >= Decimal("0.70")),
        pullback_quality=(vr_pb <= Decimal("1.3") and ctx.has_higher_low_1h),
        entry_trigger=(cur_k.close >= ema20_15m and vr_15m >= Decimal("1.2")),
        vr_impulse=bl_vr,
        vr_pullback=vr_pb,
        cl=close_location(cur_k),
    )

    pattern_desc = (
        f"K1底部启动：30d位置{float(ctx.range_pos_30d):.2f}，"
        f"1H突破量比{float(bl_vr):.2f}x收盘位{float(bl_cl):.2f}；"
        f"15m缩量{float(vr_pb):.2f}x回踩后重启{float(vr_15m):.2f}x站上EMA20"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": "OK",
        "vr_impulse": bl_vr, "vr_pullback": vr_pb, "cl": close_location(cur_k),
        "score_breakdown": sb,
    }, []
