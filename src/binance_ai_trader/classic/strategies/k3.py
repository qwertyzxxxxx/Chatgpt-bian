"""K3 高位衰竭空

Pool: Top 20 gainers (涨幅前20)
Logic: 连续上涨多日 → 高位巨量衰竭信号 → 1H结构转弱 Lower High → 15m反弹失败做空
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_from_klines, has_lower_high,
    struct_high, upper_wick_ratio, vol_grade, vol_ratio, vol_ratio_for_segment,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID   = "classic_k3"
STRATEGY_NAME = "K3 高位衰竭空"

CONDITIONS = {
    "strategy_id":  STRATEGY_ID,
    "direction":    "SHORT",
    "pool":         "Top 20 涨幅，24h成交额 ≥ 3000万 USDT",
    "time_space":  "连续上涨≥3天，7日涨幅≥30%，30日位置≥0.85，4H EMA距离≥1.5ATR",
    "exhaustion":  "1H冲顶量比≥3.0 + 长上影/创高后收回/close_location<0.50 任一",
    "1h_pattern":  "跌破1H EMA20或局部低点，随后反弹缩量，形成Lower High",
    "15m_entry":   "价格反弹到EMA20附近无法站稳，重新收盘跌破EMA20，量比≥1.2",
    "sl":          "近8根15m结构高点 + 0.2×ATR14",
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

    if ctx.direction != "SHORT":
        return None, ["direction_not_short"]

    # ── 时间+空间条件 ─────────────────────────────────────────────────────────
    if ctx.consec_direction != "UP" or ctx.consec_days < 3:
        rejs.append(f"not_enough_consec_up_days_{ctx.consec_direction}_{ctx.consec_days}<3")
    if ctx.change_7d < Decimal("30"):
        rejs.append(f"7d_gain_insufficient_{float(ctx.change_7d):.1f}%<30%")
    if ctx.range_pos_30d < Decimal("0.85"):
        rejs.append(f"not_at_high_30d_pos_{float(ctx.range_pos_30d):.2f}<0.85")
    if ctx.price_dist_4h_atr < Decimal("1.5"):
        rejs.append(f"4h_dist_too_close_{float(ctx.price_dist_4h_atr):.2f}<1.5ATR")

    # 必须排除：底部刚反弹 / 4H趋势刚转强（EMA20刚穿越EMA60）
    if ctx.ema20_4h < ctx.ema60_4h:
        rejs.append("4h_still_bearish_alignment")

    if rejs:
        return None, rejs

    # ── 1H: 量价衰竭 + 结构转弱 ─────────────────────────────────────────────
    if len(klines_1h) < 20:
        return None, ["not_enough_1h_klines"]

    # 近10根1H中找衰竭K（巨量且价格涨不动）
    recent_1h = klines_1h[-10:]
    exhaustion_bars = [
        k for k in recent_1h
        if float(k.quote_volume) > 0  # 都参与
    ]
    # 取最大量K评估
    if not exhaustion_bars:
        return None, ["no_1h_data"]

    peak_k = max(recent_1h, key=lambda k: float(k.quote_volume))
    peak_vr = vol_ratio_for_segment((peak_k,), baseline=klines_1h)
    peak_cl = close_location(peak_k)
    peak_uw = upper_wick_ratio(peak_k)

    # 衰竭条件：巨量（≥3.0x）且出现任一价格转弱信号
    has_exhaustion_vol = peak_vr >= Decimal("3.0")
    has_exhaustion_price = (
        peak_cl < Decimal("0.50") or       # 收盘偏弱
        peak_uw > Decimal("0.35")          # 长上影
    )

    if not has_exhaustion_vol:
        rejs.append(f"no_exhaustion_vol_{float(peak_vr):.2f}<3.0")
    if not has_exhaustion_price:
        rejs.append(f"no_exhaustion_price_signal_cl={float(peak_cl):.2f}_uw={float(peak_uw):.2f}")

    # 1H跌破EMA20或形成Lower High
    ema20_1h = ema_from_klines(klines_1h, 20)
    price_below_1h_ema = klines_1h[-1].close < ema20_1h
    has_lh = has_lower_high(klines_1h)

    if not price_below_1h_ema and not has_lh:
        rejs.append("1h_not_broken_ema_and_no_lower_high")

    # 随后反弹缩量（最近5根量比低于衰竭K）
    recent_5 = klines_1h[-5:]
    vr_recent = vol_ratio_for_segment(recent_5, baseline=klines_1h)
    if vr_recent > Decimal("1.5"):
        rejs.append(f"post_exhaustion_vol_still_high_{float(vr_recent):.2f}>1.5")

    if rejs:
        return None, rejs

    # ── 15m 入场：反弹到EMA20无法站稳，重新跌破 ─────────────────────────────
    if len(klines_15m) < 22:
        return None, ["not_enough_15m_klines"]

    ema20_15m = ema_from_klines(klines_15m, 20)
    atr14_15m = atr(klines_15m, 14)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]
    cl_cur    = close_location(cur_k)

    # 价格收盘跌破EMA20（做空入场信号）
    if cur_k.close >= ema20_15m:
        rejs.append("price_not_below_15m_ema20")
    if vr_15m < Decimal("1.2"):
        rejs.append(f"entry_vol_low_{float(vr_15m):.2f}<1.2")

    if rejs:
        return None, rejs

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    entry    = cur_k.close
    sl_raw   = struct_high(klines_15m, lookback=8) + CFG.sl_atr_buffer * atr14_15m
    risk     = sl_raw - entry
    if risk <= 0:
        return None, ["invalid_risk_negative"]

    stop_pct = risk / entry * 100
    if stop_pct > CFG.max_stop_pct:
        return None, [f"stop_pct_too_wide_{float(stop_pct):.2f}%>{CFG.max_stop_pct}%"]

    grade  = vol_grade(vr_15m)
    tp1_r  = CFG.tp1_r_s if grade in ("S", "S_PLUS") else CFG.tp1_r_a
    tp2_r  = CFG.tp2_r_s if grade in ("S", "S_PLUS") else CFG.tp2_r_a
    tp1    = entry - risk * tp1_r
    tp2    = entry - risk * tp2_r
    rr     = tp1_r

    sb = compute_score(
        ctx, STRATEGY_ID,
        pattern_complete=(has_exhaustion_vol and has_exhaustion_price),
        pullback_quality=(vr_recent <= Decimal("1.5") and (has_lh or price_below_1h_ema)),
        entry_trigger=(cur_k.close < ema20_15m and vr_15m >= Decimal("1.2")),
        vr_impulse=peak_vr,
        vr_pullback=vr_recent,
        cl=cl_cur,
    )

    pattern_desc = (
        f"K3高位衰竭：连续上涨{ctx.consec_days}天，7d+{float(ctx.change_7d):.1f}%，"
        f"30d位置{float(ctx.range_pos_30d):.2f}；1H量比{float(peak_vr):.2f}x"
        f"收盘位{float(peak_cl):.2f}上影{float(peak_uw):.2f}；"
        f"15m跌破EMA20量比{float(vr_15m):.2f}x"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": "OK",
        "vr_impulse": peak_vr, "vr_pullback": vr_recent, "cl": cl_cur,
        "score_breakdown": sb,
    }, []
