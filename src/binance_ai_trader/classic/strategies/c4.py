"""C4 Extreme Reversal — 极端反转

C4_TOP_SHORT: Gainers pool — exhaustion top → short
C4_BOTTOM_LONG: Losers pool — panic bottom → long
"""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, close_location, ema_from_klines, has_higher_low, has_lower_high,
    lower_wick_ratio, struct_high, struct_low, upper_wick_ratio,
    vol_grade, vol_ratio, vol_ratio_for_segment,
)
from binance_ai_trader.classic.models import CoinContext
from binance_ai_trader.classic.scoring import compute_score
from binance_ai_trader.domain.models import Kline

STRATEGY_ID_TOP = "classic_c4_top"
STRATEGY_ID_BOT = "classic_c4_bot"
STRATEGY_NAME_TOP = "C4 顶部反转空"
STRATEGY_NAME_BOT = "C4 底部反转多"


def _top_short(
    ctx: CoinContext,
    klines_15m: tuple[Kline, ...],
    klines_1h: tuple[Kline, ...],
    klines_4h: tuple[Kline, ...],
) -> tuple[dict | None, list[str]]:
    rejs: list[str] = []

    # ── Time/space conditions ─────────────────────────────────────────────────
    if ctx.consec_days < CFG.c4_top_consec_days_min:
        rejs.append(f"consec_days_{ctx.consec_days}<{CFG.c4_top_consec_days_min}")
    if ctx.change_7d < CFG.c4_top_7d_gain_min:
        rejs.append(f"7d_gain_{float(ctx.change_7d):.1f}%<{CFG.c4_top_7d_gain_min}%")
    if ctx.range_pos_30d < CFG.c4_top_30d_pos_min:
        rejs.append(f"30d_pos_{float(ctx.range_pos_30d):.2f}<{CFG.c4_top_30d_pos_min}")
    if ctx.price_dist_4h_atr < CFG.c4_top_ema_dist_min:
        rejs.append(f"ema_dist_{float(ctx.price_dist_4h_atr):.2f}ATR<{CFG.c4_top_ema_dist_min}ATR")

    # ── Exhaustion signal ─────────────────────────────────────────────────────
    # Find highest-volume 1H bar in recent 10
    recent_1h = klines_1h[-10:]
    top_bar   = max(recent_1h, key=lambda k: k.quote_volume)
    vr_top    = vol_ratio(klines_1h[:-10] + (top_bar,))
    cl_top    = close_location(top_bar)
    uw_top    = upper_wick_ratio(top_bar)

    exhaustion = (
        vr_top >= CFG.c4_top_exhaust_vol_min and (
            uw_top >= Decimal("0.30") or          # long upper wick
            cl_top <= Decimal("0.50") or           # closed mid or below
            top_bar.close <= top_bar.open          # closed red
        )
    )
    if not exhaustion:
        rejs.append(f"no_exhaustion_signal(vr={float(vr_top):.2f},cl={float(cl_top):.2f},uw={float(uw_top):.2f})")

    # ── 1H structure breakdown ────────────────────────────────────────────────
    ema20_1h = ema_from_klines(klines_1h, 20)
    if klines_1h[-1].close >= ema20_1h:
        rejs.append("price_still_above_1h_ema20")
    if not has_lower_high(klines_1h):
        rejs.append("no_lower_high_1h")

    # ── Rally volume contraction ──────────────────────────────────────────────
    vr_rally  = vol_ratio_for_segment(klines_1h[-5:])

    # ── 15m entry short ───────────────────────────────────────────────────────
    ema20_15m = ema_from_klines(klines_15m, 20)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]
    cl        = close_location(cur_k)
    atr14_15m = atr(klines_15m, 14)

    below_ema = cur_k.close < ema20_15m
    if not below_ema:
        rejs.append("15m_price_not_below_ema20")
    if vr_15m < CFG.c1_restart_vol_min:
        rejs.append(f"restart_vol_low_{float(vr_15m):.2f}")

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
        return None, [f"stop_pct_too_wide_{float(stop_pct):.2f}%"]

    grade    = vol_grade(vr_15m)
    tp1_r    = CFG.tp1_r_s_plus if grade == "S_PLUS" else CFG.tp1_r_s
    tp2_r    = CFG.tp2_r_s_plus if grade == "S_PLUS" else CFG.tp2_r_s
    tp1      = entry - risk * tp1_r
    tp2      = entry - risk * tp2_r
    rr       = tp1_r

    sb = compute_score(
        ctx, STRATEGY_ID_TOP,
        pattern_complete=exhaustion,
        pullback_quality=vr_rally <= CFG.c4_top_exhaust_vol_min and ctx.has_lower_high_1h,
        entry_trigger=below_ema,
        vr_impulse=vr_top,
        vr_pullback=vr_rally,
        cl=cl,
    )

    pattern_desc = (
        f"连涨{ctx.consec_days}天 7d+{float(ctx.change_7d):.1f}% 30d位置{float(ctx.range_pos_30d):.2f}；"
        f"顶部衰竭量比{float(vr_top):.2f}x(uw={float(uw_top):.2f} cl={float(cl_top):.2f})；"
        f"Lower High+跌破1H EMA20；15m量比{float(vr_15m):.2f}x"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": "N/A",
        "vr_impulse": vr_top, "vr_pullback": vr_rally, "cl": cl,
        "score_breakdown": sb,
    }, []


def _bot_long(
    ctx: CoinContext,
    klines_15m: tuple[Kline, ...],
    klines_1h: tuple[Kline, ...],
    klines_4h: tuple[Kline, ...],
) -> tuple[dict | None, list[str]]:
    rejs: list[str] = []

    # ── Time/space conditions ─────────────────────────────────────────────────
    if ctx.consec_days < CFG.c4_bot_consec_days_min:
        rejs.append(f"consec_days_{ctx.consec_days}<{CFG.c4_bot_consec_days_min}")
    fall_7d = abs(ctx.change_7d) if ctx.change_7d < 0 else Decimal("0")
    if fall_7d < CFG.c4_bot_7d_fall_min:
        rejs.append(f"7d_fall_{float(fall_7d):.1f}%<{CFG.c4_bot_7d_fall_min}%")
    if ctx.range_pos_30d > CFG.c4_bot_30d_pos_max:
        rejs.append(f"30d_pos_{float(ctx.range_pos_30d):.2f}>{CFG.c4_bot_30d_pos_max}")
    if ctx.price_dist_4h_atr < CFG.c4_bot_ema_dist_min:
        rejs.append(f"ema_dist_{float(ctx.price_dist_4h_atr):.2f}ATR<{CFG.c4_bot_ema_dist_min}ATR")

    # ── Panic / exhaustion signal ─────────────────────────────────────────────
    recent_1h = klines_1h[-10:]
    bot_bar   = max(recent_1h, key=lambda k: k.quote_volume)
    vr_bot    = vol_ratio(klines_1h[:-10] + (bot_bar,))
    cl_bot    = close_location(bot_bar)
    lw_bot    = lower_wick_ratio(bot_bar)

    exhaustion = (
        vr_bot >= CFG.c4_bot_panic_vol_min and (
            lw_bot >= Decimal("0.30") or           # long lower wick
            cl_bot >= Decimal("0.50") or           # closed mid or above
            bot_bar.close >= bot_bar.open           # closed green
        )
    )
    if not exhaustion:
        rejs.append(f"no_panic_signal(vr={float(vr_bot):.2f},cl={float(cl_bot):.2f},lw={float(lw_bot):.2f})")

    # ── 1H structure recovery ─────────────────────────────────────────────────
    ema20_1h = ema_from_klines(klines_1h, 20)
    if not has_higher_low(klines_1h):
        rejs.append("no_higher_low_1h")

    vr_bounce = vol_ratio_for_segment(klines_1h[-5:])

    # ── 15m entry long ────────────────────────────────────────────────────────
    ema20_15m = ema_from_klines(klines_15m, 20)
    vr_15m    = vol_ratio(klines_15m)
    cur_k     = klines_15m[-1]
    cl        = close_location(cur_k)
    atr14_15m = atr(klines_15m, 14)

    above_ema = cur_k.close > ema20_15m
    if not above_ema:
        rejs.append("15m_price_not_above_ema20")
    if vr_15m < CFG.c1_restart_vol_min:
        rejs.append(f"restart_vol_low_{float(vr_15m):.2f}")

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
        return None, [f"stop_pct_too_wide_{float(stop_pct):.2f}%"]

    grade    = vol_grade(vr_15m)
    tp1_r    = CFG.tp1_r_s_plus if grade == "S_PLUS" else CFG.tp1_r_s
    tp2_r    = CFG.tp2_r_s_plus if grade == "S_PLUS" else CFG.tp2_r_s
    tp1      = entry + risk * tp1_r
    tp2      = entry + risk * tp2_r
    rr       = tp1_r

    sb = compute_score(
        ctx, STRATEGY_ID_BOT,
        pattern_complete=exhaustion,
        pullback_quality=vr_bounce <= Decimal("1.0") and ctx.has_higher_low_1h,
        entry_trigger=above_ema,
        vr_impulse=vr_bot,
        vr_pullback=vr_bounce,
        cl=cl,
    )

    pattern_desc = (
        f"连跌{ctx.consec_days}天 7d-{float(fall_7d):.1f}% 30d位置{float(ctx.range_pos_30d):.2f}；"
        f"底部恐慌量比{float(vr_bot):.2f}x(lw={float(lw_bot):.2f} cl={float(cl_bot):.2f})；"
        f"Higher Low+站上1H EMA20；15m量比{float(vr_15m):.2f}x"
    )

    return {
        "entry": entry, "sl": sl_raw, "tp1": tp1, "tp2": tp2, "rr": rr,
        "stop_pct": stop_pct, "score": sb.total, "vol_grade": grade,
        "pattern_desc": pattern_desc, "block_checks": "N/A",
        "vr_impulse": vr_bot, "vr_pullback": vr_bounce, "cl": cl,
        "score_breakdown": sb,
    }, []


def evaluate_top(ctx, klines_15m, klines_1h, klines_4h):
    return _top_short(ctx, klines_15m, klines_1h, klines_4h)


def evaluate_bot(ctx, klines_15m, klines_1h, klines_4h):
    return _bot_long(ctx, klines_15m, klines_1h, klines_4h)
