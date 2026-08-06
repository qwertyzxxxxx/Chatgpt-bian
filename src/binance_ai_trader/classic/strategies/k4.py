"""K4 恐慌反转多

Pool: Top 20 losers (跌幅前20)
Logic: 连续下跌多日 → 低位恐慌巨量 → 跌不动/假跌破收回 → 1H Higher Low → 15m确认做多
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_from_klines, has_higher_low,
    lower_wick_ratio, struct_low, vol_grade, vol_ratio, vol_ratio_for_segment,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID   = "classic_k4"
STRATEGY_NAME = "K4 恐慌反转多"

CONDITIONS = {
    "strategy_id":  STRATEGY_ID,
    "direction":    "LONG",
    "pool":         "Top 20 跌幅，24h成交额 ≥ 3000万 USDT",
    "time_space":  "连续下跌≥3天，7日跌幅≤-25%，30日位置≤0.15，4H EMA距离≥1.5ATR（价格低于EMA20）",
    "panic":       "1H恐慌量比≥3.0 + 长下影/假跌破收回/close_location>0.50 任一",
    "1h_pattern":  "停止连续创新低，形成Higher Low，重新站上1H EMA20或突破局部高点",
    "15m_entry":   "回踩不破前低，形成Higher Low，站上EMA20，量比≥1.2",
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

    # ── 时间+空间条件 ─────────────────────────────────────────────────────────
    if ctx.consec_direction != "DOWN" or ctx.consec_days < 3:
        rejs.append(f"not_enough_consec_down_days_{ctx.consec_direction}_{ctx.consec_days}<3")
    if ctx.change_7d > Decimal("-25"):
        rejs.append(f"7d_fall_insufficient_{float(ctx.change_7d):.1f}%>-25%")
    if ctx.range_pos_30d > Decimal("0.15"):
        rejs.append(f"30d_pos_not_low_{float(ctx.range_pos_30d):.2f}>0.15")
    # 价格低于4H EMA20至少1.5 ATR
    if ctx.current_price >= ctx.ema20_4h:
        rejs.append("price_above_4h_ema20_not_oversold")
    if ctx.price_dist_4h_atr < Decimal("1.5"):
        rejs.append(f"4h_dist_too_close_{float(ctx.price_dist_4h_atr):.2f}<1.5ATR")

    # 排除：4H仍在连续创新低（EMA20仍在EMA60上方时不是真底部）
    # 允许 4H EMA20 < EMA60（下跌趋势，才是真超跌）
    if rejs:
        return None, rejs

    # ── 1H: 恐慌量价衰竭 + 结构转强 ─────────────────────────────────────────
    if len(klines_1h) < 20:
        return None, ["not_enough_1h_klines"]

    # 近10根1H中找恐慌K（巨量且价格跌不动）
    recent_1h = klines_1h[-10:]
    panic_k = max(recent_1h, key=lambda k: float(k.quote_volume))
    panic_vr = vol_ratio_for_segment((panic_k,), baseline=klines_1h)
    panic_cl = close_location(panic_k)
    panic_lw = lower_wick_ratio(panic_k)

    # 恐慌条件：巨量（≥3.0x）且出现任一价格转强信号
    has_panic_vol   = panic_vr >= Decimal("3.0")
    has_panic_price = (
        panic_cl > Decimal("0.50") or      # 收盘偏强（跌不动）
        panic_lw > Decimal("0.35")         # 长下影（假跌破收回）
    )

    if not has_panic_vol:
        rejs.append(f"no_panic_vol_{float(panic_vr):.2f}<3.0")
    if not has_panic_price:
        rejs.append(f"no_panic_reversal_signal_cl={float(panic_cl):.2f}_lw={float(panic_lw):.2f}")

    # 1H停止连续创新低 + Higher Low
    has_hl_1h = has_higher_low(klines_1h)
    if not has_hl_1h:
        rejs.append("no_higher_low_1h")

    # 1H站上EMA20或最近5根中有站上过
    ema20_1h_val = ema_from_klines(klines_1h, 20)
    price_above_1h_ema = klines_1h[-1].close >= ema20_1h_val
    recent_above = any(k.close >= ema20_1h_val for k in klines_1h[-5:])
    if not price_above_1h_ema and not recent_above:
        rejs.append("price_never_reclaimed_1h_ema20_recently")

    if rejs:
        return None, rejs

    # ── 15m 入场确认 ──────────────────────────────────────────────────────────
    if len(klines_15m) < 22:
        return None, ["not_enough_15m_klines"]

    ema20_15m = ema_from_klines(klines_15m, 20)
    atr14_15m = atr(klines_15m, 14)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]
    cl_cur    = close_location(cur_k)

    # 当前K线站上15m EMA20
    if cur_k.close < ema20_15m:
        rejs.append("price_below_15m_ema20")
    # 再启动量能
    if vr_15m < Decimal("1.2"):
        rejs.append(f"restart_vol_low_{float(vr_15m):.2f}<1.2")
    # 15m Higher Low（回踩没破前低）
    if not has_higher_low(klines_15m):
        rejs.append("no_higher_low_15m")

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
        pattern_complete=(has_panic_vol and has_panic_price),
        pullback_quality=(has_hl_1h and recent_above),
        entry_trigger=(cur_k.close >= ema20_15m and vr_15m >= Decimal("1.2")),
        vr_impulse=panic_vr,
        vr_pullback=Decimal("1.0"),   # pullback vol not primary for K4
        cl=cl_cur,
    )

    pattern_desc = (
        f"K4恐慌反转：连续下跌{ctx.consec_days}天，7d{float(ctx.change_7d):.1f}%，"
        f"30d位置{float(ctx.range_pos_30d):.2f}；1H量比{float(panic_vr):.2f}x"
        f"收盘位{float(panic_cl):.2f}下影{float(panic_lw):.2f}；"
        f"15m站上EMA20量比{float(vr_15m):.2f}x"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": "OK",
        "vr_impulse": panic_vr, "vr_pullback": Decimal("1.0"), "cl": cl_cur,
        "score_breakdown": sb,
    }, []
