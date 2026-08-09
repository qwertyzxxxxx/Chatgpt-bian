"""K4v2 恐慌反轉多 — 擴展候選池 + 2/3 成熟度門檻

改動 vs K4：
  池：24h跌幅前20 ∪ 7d跌幅前20 ∪ 30d位置最低前20（scanner 負責構建）
  空間：range_pos_30d <= 0.20（原 0.15 放寬）
  成熟度：3 項滿足 2 項即可（原要求連跌 ≥3 天硬門檻）
    ① trend_age_days >= 3
    ② ret_7d <= -25%
    ③ distance_4h_ema20 >= 1.5 ATR（價格低於 EMA20）
  後續 1H 恐慌 / 結構 / 15m 確認：完全不降低，與 K4 完全相同。

拒絕原因統一加前綴供 scanner 按階段統計：
  SPACE_    空間硬門檻
  MATURITY_ 2/3 成熟度
  PANIC_    1H 恐慌量價
  STRUCT_   1H 結構
  ENTRY_    15m 入場
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_from_klines, has_higher_low,
    lower_wick_ratio, struct_low, trend_age_from_swing_high,
    vol_grade, vol_ratio, vol_ratio_for_segment,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID   = "classic_k4v2"
STRATEGY_NAME = "K4v2 恐慌反轉多+"

CONDITIONS = {
    "strategy_id":  STRATEGY_ID,
    "direction":    "LONG",
    "pool":         "24h跌前20 ∪ 7d跌前20 ∪ 30d最低位前20，成交額≥3000萬",
    "space":        "30日位置 ≤ 0.20",
    "maturity":     "3項滿足2項：trend_age≥3d / 7d≤-25% / 4H距EMA≥1.5ATR",
    "panic":        "1H量比≥3.0x + 長下影/跌不動任一",
    "structure":    "1H停止創新低，HL，重新站上EMA20",
    "entry":        "15m站上EMA20，HL，量比≥1.2x",
    "sl":           "近8根15m最低點 - 0.2×ATR14",
    "tp":           "A級2.0R / S級2.5R / S+級3.0R",
    "hold_hours":   48,
}


def evaluate(
    ctx: CoinContext,
    klines_15m: tuple[Kline, ...],
    klines_1h: tuple[Kline, ...],
    klines_4h: tuple[Kline, ...],
    klines_1d: tuple[Kline, ...],
) -> tuple[dict | None, list[str]]:

    if ctx.direction != "LONG":
        return None, ["direction_not_long"]

    # ── Stage 1: 空間硬門檻 ───────────────────────────────────────────────────
    if ctx.range_pos_30d > Decimal("0.20"):
        return None, [f"SPACE_range_pos_{float(ctx.range_pos_30d):.2f}>0.20"]

    # ── Stage 2: 成熟度 2/3 ───────────────────────────────────────────────────
    trend_age, trend_return = trend_age_from_swing_high(klines_1d, window=2)

    mat_flags = 0
    mat_ok: list[str] = []
    mat_fail: list[str] = []

    if trend_age >= 3:
        mat_flags += 1
        mat_ok.append(f"age={trend_age}d")
    else:
        mat_fail.append(f"age={trend_age}<3d")

    if ctx.change_7d <= Decimal("-25"):
        mat_flags += 1
        mat_ok.append(f"7d={float(ctx.change_7d):.1f}%")
    else:
        mat_fail.append(f"7d={float(ctx.change_7d):.1f}%>-25%")

    if ctx.current_price < ctx.ema20_4h and ctx.price_dist_4h_atr >= Decimal("1.5"):
        mat_flags += 1
        mat_ok.append(f"dist={float(ctx.price_dist_4h_atr):.2f}ATR_below")
    else:
        if ctx.current_price >= ctx.ema20_4h:
            mat_fail.append("price_above_4h_ema20")
        else:
            mat_fail.append(f"dist={float(ctx.price_dist_4h_atr):.2f}<1.5ATR")

    if mat_flags < 2:
        return None, [f"MATURITY_{mat_flags}/3_fail: {'; '.join(mat_fail)}"]

    # ── Stage 3: 1H 恐慌量價衰竭 ─────────────────────────────────────────────
    if len(klines_1h) < 20:
        return None, ["PANIC_not_enough_1h_klines"]

    recent_1h = klines_1h[-10:]
    if not recent_1h:
        return None, ["PANIC_no_1h_data"]

    panic_k  = max(recent_1h, key=lambda k: float(k.quote_volume))
    panic_vr = vol_ratio_for_segment((panic_k,), baseline=klines_1h)
    panic_cl = close_location(panic_k)
    panic_lw = lower_wick_ratio(panic_k)

    has_panic_vol   = panic_vr >= Decimal("3.0")
    has_panic_price = panic_cl > Decimal("0.50") or panic_lw > Decimal("0.35")

    if not has_panic_vol:
        return None, [f"PANIC_vol_{float(panic_vr):.2f}<3.0"]
    if not has_panic_price:
        return None, [f"PANIC_price_cl={float(panic_cl):.2f}_lw={float(panic_lw):.2f}"]

    # ── Stage 4: 1H 結構確認 ─────────────────────────────────────────────────
    has_hl_1h = has_higher_low(klines_1h)
    if not has_hl_1h:
        return None, ["STRUCT_no_higher_low_1h"]

    ema20_1h_val = ema_from_klines(klines_1h, 20)
    recent_above = any(k.close >= ema20_1h_val for k in klines_1h[-5:])
    if not recent_above:
        return None, ["STRUCT_price_never_reclaimed_1h_ema20"]

    # ── Stage 5: 15m 入場確認 ─────────────────────────────────────────────────
    if len(klines_15m) < 22:
        return None, ["ENTRY_not_enough_15m_klines"]

    ema20_15m = ema_from_klines(klines_15m, 20)
    atr14_15m = atr(klines_15m, 14)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]
    cl_cur    = close_location(cur_k)

    if cur_k.close < ema20_15m:
        return None, ["ENTRY_price_below_15m_ema20"]
    if vr_15m < Decimal("1.2"):
        return None, [f"ENTRY_vol_low_{float(vr_15m):.2f}<1.2"]
    if not has_higher_low(klines_15m):
        return None, ["ENTRY_no_higher_low_15m"]

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    entry    = cur_k.close
    sl_raw   = struct_low(klines_15m, lookback=8) - CFG.sl_atr_buffer * atr14_15m
    risk     = entry - sl_raw
    if risk <= 0:
        return None, ["ENTRY_invalid_risk_negative"]

    stop_pct = risk / entry * 100
    if stop_pct > CFG.max_stop_pct:
        return None, [f"ENTRY_stop_pct_wide_{float(stop_pct):.2f}%>{CFG.max_stop_pct}%"]

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
        vr_pullback=Decimal("1.0"),
        cl=cl_cur,
    )

    mat_summary = "+".join(mat_ok)
    pattern_desc = (
        f"K4v2恐慌反轉：30d位置{float(ctx.range_pos_30d):.2f}，"
        f"成熟度[{mat_summary}]，趨勢持續{trend_age}d回撤{float(trend_return):.1f}%；"
        f"1H量比{float(panic_vr):.2f}x收盤位{float(panic_cl):.2f}下影{float(panic_lw):.2f}；"
        f"15m站上EMA20量比{float(vr_15m):.2f}x"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": f"maturity={mat_flags}/3",
        "trend_age_days": trend_age, "trend_return_pct": float(trend_return),
        "vr_impulse": panic_vr, "vr_pullback": Decimal("1.0"), "cl": cl_cur,
        "score_breakdown": sb,
    }, []
