"""Telegram push formatter for Classic C1-C4 signals."""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.notifications import TelegramNotifier

_SCORING_AVAILABLE: bool
try:
    from binance_ai_trader.v3.scoring.formatter import format_score_block_classic
    from binance_ai_trader.v3.scoring.models import (
        SCORE_VERSION,
        UnifiedScore,
        rr_score_pts,
        score_grade,
    )
    _SCORING_AVAILABLE = True
except Exception:
    _SCORING_AVAILABLE = False


_VOL_GRADE_EMOJI = {
    "S_PLUS":    "🔥🔥",
    "S":         "🔥",
    "A":         "⭐",
    "NORMAL":    "📊",
    "WEAK":      "💤",
    "EXHAUSTION":"⚡",
}

_DIRECTION_EMOJI = {
    "LONG":  "📗 做多",
    "SHORT": "📕 做空",
}


def _pct(v) -> str:
    return f"{float(v):+.2f}%"


def _price(v) -> str:
    f = float(v)
    if f >= 100:
        return f"{f:.2f}"
    if f >= 1:
        return f"{f:.4f}"
    return f"{f:.6f}"


def _unified_from_classic(sig: dict) -> "UnifiedScore | None":
    """Map old ScoreBreakdown to unified 5-category score.

    C1-4 now attach 'score_breakdown' (ScoreBreakdown) to the signal dict.
    We map it onto the new categories and add a fresh RR score.
    This keeps ONE scoring system for Classic — no duplication.
    """
    if not _SCORING_AVAILABLE:
        return None
    sb = sig.get("score_breakdown")
    if sb is None:
        return None
    try:
        stop_pct = float(sig.get("stop_pct", 0))
        rr       = float(sig.get("rr", 0))

        # Map old 4 categories → new 5 categories
        # volume:          old max 20 → new max 30  (×1.5)
        vol_score    = min(30, round(float(sb.volume) * 1.5))
        # trend_structure: old max 25 → new max 25  (same)
        trend_score  = int(sb.trend)
        # entry_position:  old time_space max 30 → new max 20  (×2/3)
        pos_score    = min(20, round(float(sb.time_space) * 2 / 3))
        # risk_reward:     new computation from stop_pct + rr
        rr_score     = rr_score_pts(stop_pct, rr)
        # strategy_fit:    old pattern max 25 → new max 10  (×0.4)
        fit_score    = min(10, round(float(sb.pattern) * 0.4))

        total = min(100, vol_score + trend_score + pos_score + rr_score + fit_score)
        grade = score_grade(total)

        return UnifiedScore(
            score_total=total,
            score_grade=grade,
            score_version=SCORE_VERSION,
            volume_score=vol_score,
            trend_structure_score=trend_score,
            entry_position_score=pos_score,
            risk_reward_score=rr_score,
            strategy_fit_score=fit_score,
            score_summary=_classic_summary(vol_score, trend_score, pos_score, rr_score, fit_score),
            score_details={
                "mapped_from":   "classic_ScoreBreakdown",
                "old_time_space": float(sb.time_space),
                "old_trend":      float(sb.trend),
                "old_pattern":    float(sb.pattern),
                "old_volume":     float(sb.volume),
                "old_total":      float(sb.total),
                "stop_pct":       stop_pct,
                "rr":             rr,
            },
        )
    except Exception:
        return None


def _classic_summary(vol: int, trend: int, pos: int, rr: int, fit: int) -> str:
    cats = [
        ("量能",     vol,   30),
        ("趋势结构", trend, 25),
        ("入场位置", pos,   20),
        ("风险收益", rr,    15),
        ("策略匹配", fit,   10),
    ]
    ratios = [(name, pts / mx) for name, pts, mx in cats if mx > 0]
    best   = max(ratios, key=lambda x: x[1])
    worst  = min(ratios, key=lambda x: x[1])
    lines  = []
    _STR = {
        "量能":     "量能充沛，资金关注度高",
        "趋势结构": "多周期趋势结构清晰",
        "入场位置": "入场时机较佳，空间合理",
        "风险收益": "风险收益比良好",
        "策略匹配": "信号符合策略核心条件",
    }
    _RSK = {
        "量能":     "量能偏弱，注意成交额支撑",
        "趋势结构": "趋势支撑不足，需关注方向变化",
        "入场位置": "当前位置可能偏高/偏低，追入需谨慎",
        "风险收益": "前方压力支撑较近，真实空间有限",
        "策略匹配": "条件勉强达标，信号强度一般",
    }
    if best[1] >= 0.7:
        lines.append(_STR.get(best[0], ""))
    if worst[1] < 0.5:
        lines.append(_RSK.get(worst[0], ""))
    return "\n".join(l for l in lines if l)


def send_classic_signal(notifier: TelegramNotifier, sig: dict) -> None:
    """Send a formatted Classic strategy signal to Telegram."""
    direction_label = _DIRECTION_EMOJI.get(sig["direction"], sig["direction"])
    vol_em = _VOL_GRADE_EMOJI.get(sig.get("vol_grade", ""), "")
    entry   = sig["entry"]
    sl      = sig["sl"]
    tp1     = sig["tp1"]
    tp2     = sig["tp2"]
    rr      = sig["rr"]
    stop_pct = sig.get("stop_pct", 0)

    unified = _unified_from_classic(sig) if _SCORING_AVAILABLE else None

    if unified is not None:
        score_display = f"{unified.score_total}/100  {unified.score_grade}"
    else:
        score_display = f"{sig.get('score', 0)}/100"

    msg = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"【{sig['strategy_id'].upper()} {sig['strategy_name']}】📋 模拟盘\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 {sig['symbol']}  {direction_label}\n"
        f"📦 池: {sig['pool_type']} #{sig['pool_rank']}\n"
        f"\n"
        f"💰 入场: {_price(entry)}\n"
        f"🛑 止损: {_price(sl)}  ({float(stop_pct):.1f}%)\n"
        f"🎯 TP1:  {_price(tp1)}  ({float(rr):.1f}R)\n"
        f"🎯 TP2:  {_price(tp2)}\n"
        f"\n"
        f"📊 量价等级: {vol_em} {sig.get('vol_grade','')}\n"
        f"   1H量比: {float(sig.get('vol_ratio_1h',0)):.2f}x\n"
        f"   15m量比: {float(sig.get('vol_ratio_15m',0)):.2f}x\n"
        f"\n"
        f"📈 3日涨跌: {_pct(sig.get('change_3d',0))}\n"
        f"📈 7日涨跌: {_pct(sig.get('change_7d',0))}\n"
        f"📈 24h涨跌: {_pct(sig.get('change_24h',0))}\n"
        f"📍 30日位置: {float(sig.get('range_pos_30d',0)):.2f}\n"
        f"📅 连续天数: {sig.get('consec_days',0)}天\n"
        f"📏 距4H EMA20: {float(sig.get('dist_4h_ema_atr',0)):.2f} ATR\n"
        f"\n"
        f"🔍 图形: {sig.get('pattern_desc','')}\n"
        f"✅ 禁止条件: {sig.get('block_checks','OK')}\n"
        f"🏆 评分: {score_display}\n"
    )

    if unified is not None and _SCORING_AVAILABLE:
        msg += format_score_block_classic(unified)

    notifier.send(msg)
