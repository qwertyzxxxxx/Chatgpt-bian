"""K3v2 高位衰竭空 — 擴展候選池 + 2/3 成熟度門檻

改動 vs K3：
  池：24h漲幅前20 ∪ 7d漲幅前20 ∪ 30d位置最高前20（scanner 負責構建）
  空間：range_pos_30d >= 0.80（原 0.85 放寬）
  成熟度：3 項滿足 2 項即可（原要求連漲 ≥3 天硬門檻）
    ① trend_age_days >= 3
    ② ret_7d >= 30%
    ③ distance_4h_ema20 >= 1.5 ATR
  後續 1H 衰竭 / 結構 / 15m 入場：完全不降低，與 K3 完全相同。

拒絕原因統一加前綴供 scanner 按階段統計：
  SPACE_    空間硬門檻
  MATURITY_ 2/3 成熟度
  EXHAUST_  1H 量價衰竭
  STRUCT_   1H 結構
  ENTRY_    15m 入場
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_from_klines, has_lower_high,
    struct_high, trend_age_from_swing_low, upper_wick_ratio,
    vol_grade, vol_ratio, vol_ratio_for_segment,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID   = "classic_k3v2"
STRATEGY_NAME = "K3v2 高位衰竭空+"

CONDITIONS = {
    "strategy_id":  STRATEGY_ID,
    "direction":    "SHORT",
    "pool":         "24h漲前20 ∪ 7d漲前20 ∪ 30d最高位前20，成交額≥3000萬",
    "space":        "30日位置 ≥ 0.80",
    "maturity":     "3項滿足2項：trend_age≥3d / 7d≥30% / 4H距EMA≥1.5ATR",
    "exhaustion":   "1H量比≥3.0x + 長上影/收盤偏弱任一",
    "structure":    "1H跌破EMA20或形成LH，反彈縮量",
    "entry":        "15m收盤跌破EMA20，量比≥1.2x",
    "sl":           "近8根15m最高點 + 0.2×ATR14",
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

    if ctx.direction != "SHORT":
        return None, ["direction_not_short"]

    # ── Stage 1: 空間硬門檻 ───────────────────────────────────────────────────
    if ctx.range_pos_30d < Decimal("0.80"):
        return None, [f"SPACE_range_pos_{float(ctx.range_pos_30d):.2f}<0.80"]

    # ── Stage 2: 成熟度 2/3 ───────────────────────────────────────────────────
    trend_age, trend_return = trend_age_from_swing_low(klines_1d, window=2)

    mat_flags = 0
    mat_ok: list[str] = []
    mat_fail: list[str] = []

    if trend_age >= 3:
        mat_flags += 1
        mat_ok.append(f"age={trend_age}d")
    else:
        mat_fail.append(f"age={trend_age}<3d")

    if ctx.change_7d >= Decimal("30"):
        mat_flags += 1
        mat_ok.append(f"7d={float(ctx.change_7d):.1f}%")
    else:
        mat_fail.append(f"7d={float(ctx.change_7d):.1f}%<30%")

    if ctx.price_dist_4h_atr >= Decimal("1.5"):
        mat_flags += 1
        mat_ok.append(f"dist={float(ctx.price_dist_4h_atr):.2f}ATR")
    else:
        mat_fail.append(f"dist={float(ctx.price_dist_4h_atr):.2f}<1.5ATR")

    if mat_flags < 2:
        return None, [f"MATURITY_{mat_flags}/3_fail: {'; '.join(mat_fail)}"]

    # ── Stage 3: 1H 量價衰竭 ──────────────────────────────────────────────────
    if len(klines_1h) < 20:
        return None, ["EXHAUST_not_enough_1h_klines"]

    recent_1h = klines_1h[-10:]
    if not recent_1h:
        return None, ["EXHAUST_no_1h_data"]

    peak_k  = max(recent_1h, key=lambda k: float(k.quote_volume))
    peak_vr = vol_ratio_for_segment((peak_k,), baseline=klines_1h)
    peak_cl = close_location(peak_k)
    peak_uw = upper_wick_ratio(peak_k)

    has_exhaust_vol   = peak_vr >= Decimal("3.0")
    has_exhaust_price = peak_cl < Decimal("0.50") or peak_uw > Decimal("0.35")

    if not has_exhaust_vol:
        return None, [f"EXHAUST_vol_{float(peak_vr):.2f}<3.0"]
    if not has_exhaust_price:
        return None, [f"EXHAUST_price_cl={float(peak_cl):.2f}_uw={float(peak_uw):.2f}"]

    # ── Stage 4: 1H 結構確認 ─────────────────────────────────────────────────
    ema20_1h = ema_from_klines(klines_1h, 20)
    price_below_1h_ema = klines_1h[-1].close < ema20_1h
    has_lh = has_lower_high(klines_1h)

    if not price_below_1h_ema and not has_lh:
        return None, ["STRUCT_no_1h_break_or_lower_high"]

    recent_5  = klines_1h[-5:]
    vr_recent = vol_ratio_for_segment(recent_5, baseline=klines_1h)
    if vr_recent > Decimal("1.5"):
        return None, [f"STRUCT_post_exhaust_vol_high_{float(vr_recent):.2f}>1.5"]

    # ── Stage 5: 15m 入場確認 ─────────────────────────────────────────────────
    if len(klines_15m) < 22:
        return None, ["ENTRY_not_enough_15m_klines"]

    ema20_15m = ema_from_klines(klines_15m, 20)
    atr14_15m = atr(klines_15m, 14)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]
    cl_cur    = close_location(cur_k)

    if cur_k.close >= ema20_15m:
        return None, ["ENTRY_price_not_below_15m_ema20"]
    if vr_15m < Decimal("1.2"):
        return None, [f"ENTRY_vol_low_{float(vr_15m):.2f}<1.2"]

    # ── Entry / SL / TP ───────────────────────────────────────────────────────
    entry    = cur_k.close
    sl_raw   = struct_high(klines_15m, lookback=8) + CFG.sl_atr_buffer * atr14_15m
    risk     = sl_raw - entry
    if risk <= 0:
        return None, ["ENTRY_invalid_risk_negative"]

    stop_pct = risk / entry * 100
    if stop_pct > CFG.max_stop_pct:
        return None, [f"ENTRY_stop_pct_wide_{float(stop_pct):.2f}%>{CFG.max_stop_pct}%"]

    grade  = vol_grade(vr_15m)
    tp1_r  = CFG.tp1_r_s if grade in ("S", "S_PLUS") else CFG.tp1_r_a
    tp2_r  = CFG.tp2_r_s if grade in ("S", "S_PLUS") else CFG.tp2_r_a
    tp1    = entry - risk * tp1_r
    tp2    = entry - risk * tp2_r
    rr     = tp1_r

    sb = compute_score(
        ctx, STRATEGY_ID,
        pattern_complete=(has_exhaust_vol and has_exhaust_price),
        pullback_quality=(vr_recent <= Decimal("1.5") and (has_lh or price_below_1h_ema)),
        entry_trigger=(cur_k.close < ema20_15m and vr_15m >= Decimal("1.2")),
        vr_impulse=peak_vr,
        vr_pullback=vr_recent,
        cl=cl_cur,
    )

    mat_summary = "+".join(mat_ok)
    pattern_desc = (
        f"K3v2高位衰竭：30d位置{float(ctx.range_pos_30d):.2f}，"
        f"成熟度[{mat_summary}]，趨勢持續{trend_age}d漲幅{float(trend_return):.1f}%；"
        f"1H量比{float(peak_vr):.2f}x收盤位{float(peak_cl):.2f}上影{float(peak_uw):.2f}；"
        f"15m跌破EMA20量比{float(vr_15m):.2f}x"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": f"maturity={mat_flags}/3",
        "trend_age_days": trend_age, "trend_return_pct": float(trend_return),
        "vr_impulse": peak_vr, "vr_pullback": vr_recent, "cl": cl_cur,
        "score_breakdown": sb,
    }, []
