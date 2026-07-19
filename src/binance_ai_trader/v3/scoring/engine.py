"""Unified scoring engine — SCORE_V1_UNIFIED.

score_signal(candidate, klines) → UnifiedScore
score_signal_with_client(candidate, client) → UnifiedScore | None

klines dict keys: "1d", "4h", "1h", "15m"
Each value is a tuple of CLOSED Kline objects (excluding the forming bar).

Volume semantics differ by strategy:
  Breakout/trend  (V3/V66/V662/V663/wave): high M15 ratio is good.
  Pullback/quiet  (V664): low M15 ratio is good (volume contraction confirms pullback).

Scoring failure never raises — returns None from score_signal_with_client.
"""
from __future__ import annotations

import logging
import statistics
from decimal import Decimal

from binance_ai_trader.v3.scoring.models import (
    SCORE_VERSION,
    UnifiedScore,
    rr_score_pts,
    score_grade,
)

log = logging.getLogger(__name__)


# ── public entry points ───────────────────────────────────────────────────────

def score_signal(candidate, klines: dict | None = None) -> UnifiedScore:
    """Compute unified score.  Never returns None; raises on error.

    candidate: V3Candidate or CandidateInput (must have stop_pct, rr, etc.)
    klines: {"1d": tuple[Kline,...], "4h": ..., "1h": ..., "15m": ...}
            Omit or pass None to fall back to candidate-only scoring.
    """
    sid       = candidate.strategy_id
    direction = candidate.direction

    vol_score,   vol_det   = _volume_score(candidate, klines)
    trend_score, trend_det = _trend_score(candidate, klines, direction)
    pos_score,   pos_det   = _entry_pos_score(candidate, klines, direction)
    rr_score,    rr_det    = _rr_score(candidate)
    fit_score,   fit_det   = _strategy_fit_score(candidate, klines)

    total = min(100, vol_score + trend_score + pos_score + rr_score + fit_score)
    grade = score_grade(total)

    details = {
        "volume":          vol_det,
        "trend_structure": trend_det,
        "entry_position":  pos_det,
        "risk_reward":     rr_det,
        "strategy_fit":    fit_det,
        "strategy_id":     sid,
        "direction":       direction,
        "adapter":         _adapter_name(sid),
        "score_version":   SCORE_VERSION,
    }
    summary = _generate_summary(vol_score, trend_score, pos_score, rr_score, fit_score)

    return UnifiedScore(
        score_total=total,
        score_grade=grade,
        score_version=SCORE_VERSION,
        volume_score=vol_score,
        trend_structure_score=trend_score,
        entry_position_score=pos_score,
        risk_reward_score=rr_score,
        strategy_fit_score=fit_score,
        score_summary=summary,
        score_details=details,
    )


def score_signal_with_client(candidate, client) -> UnifiedScore | None:
    """Fetch klines, score, save to DB.  Returns None on any failure.

    Designed to be called from V3TelegramNotifier.send_candidate().
    All exceptions are caught — original signal push is never blocked.
    """
    try:
        klines = {
            "1d":  client.klines(candidate.symbol, "1d",  51)[:-1],
            "4h":  client.klines(candidate.symbol, "4h",  61)[:-1],
            "1h":  client.klines(candidate.symbol, "1h",  61)[:-1],
            "15m": client.klines(candidate.symbol, "15m", 41)[:-1],
        }
        unified = score_signal(candidate, klines)
        _save_score(candidate.signal_id, unified)
        return unified
    except Exception as exc:
        log.debug(
            "[scoring] %s %s: %s — continuing without score",
            getattr(candidate, "signal_id", "?"),
            getattr(candidate, "symbol", "?"),
            exc,
        )
        return None


def _save_score(signal_id: str, score: UnifiedScore) -> None:
    import json
    from binance_ai_trader.v3.storage.pg import get_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE v3_candidates
                   SET score_total=%s, score_grade=%s, score_version=%s,
                       volume_score=%s, trend_structure_score=%s,
                       entry_position_score=%s, risk_reward_score=%s,
                       strategy_fit_score=%s,
                       score_summary=%s, score_details_json=%s, scored_at=%s
                   WHERE signal_id=%s""",
                (
                    score.score_total, score.score_grade, score.score_version,
                    score.volume_score, score.trend_structure_score,
                    score.entry_position_score, score.risk_reward_score,
                    score.strategy_fit_score,
                    score.score_summary,
                    json.dumps(score.score_details, default=str),
                    score.scored_at,
                    signal_id,
                ),
            )
        conn.commit()
    except Exception as exc:
        log.debug("[scoring] DB save failed for %s: %s", signal_id, exc)
    finally:
        conn.close()


# ── category scorers ─────────────────────────────────────────────────────────

def _volume_score(candidate, klines: dict | None) -> tuple[int, dict]:
    """0–30 pts. Pullback (V664) inverts M15 vol ratio scoring."""
    sid = candidate.strategy_id
    is_pullback = "v664" in sid
    details: dict = {}
    pts = 0

    # 1. 24h quote volume (0–10)
    qv = getattr(candidate, "quote_volume", None)
    if qv:
        if   qv >= 100_000_000: qv_pts = 10
        elif qv >= 50_000_000:  qv_pts = 8
        elif qv >= 20_000_000:  qv_pts = 6
        elif qv >= 10_000_000:  qv_pts = 4
        elif qv >= 5_000_000:   qv_pts = 2
        else:                    qv_pts = 0
        details["quote_volume_24h"] = round(qv)
        details["quote_volume_pts"] = qv_pts
        pts += qv_pts

    # 2. M15 vol ratio (0–10)
    vr = getattr(candidate, "volume_ratio", None)
    if vr is not None:
        if is_pullback:
            if   vr < 0.6: vr_pts = 10
            elif vr < 0.8: vr_pts = 8
            elif vr < 1.0: vr_pts = 6
            elif vr < 1.2: vr_pts = 4
            else:           vr_pts = 2
        else:
            if   vr >= 4.0: vr_pts = 10
            elif vr >= 3.0: vr_pts = 8
            elif vr >= 2.0: vr_pts = 7
            elif vr >= 1.5: vr_pts = 6
            elif vr >= 1.2: vr_pts = 4
            elif vr >= 1.0: vr_pts = 2
            else:            vr_pts = 0
        details["m15_vol_ratio"] = round(float(vr), 2)
        details["m15_vol_ratio_pts"] = vr_pts
        pts += vr_pts

    # 3. H1 volume continuity (0–6) — klines required
    if klines and "1h" in klines:
        k1h = klines["1h"]
        if len(k1h) >= 13:
            recent    = k1h[-3:]
            prior     = k1h[-13:-3]
            prior_med = statistics.median([float(k.quote_volume) for k in prior]) if prior else 0
            rec_avg   = sum(float(k.quote_volume) for k in recent) / len(recent) if recent else 0
            if prior_med > 0:
                h1_r = rec_avg / prior_med
                if   h1_r >= 1.5: h1_pts = 6
                elif h1_r >= 1.2: h1_pts = 4
                elif h1_r >= 1.0: h1_pts = 2
                else:              h1_pts = 0
                details["h1_vol_ratio"] = round(h1_r, 2)
                details["h1_vol_pts"]   = h1_pts
                pts += h1_pts

    # 4. M15 bar quality / body ratio (0–4) — klines required
    if klines and "15m" in klines:
        k15 = klines["15m"]
        if k15:
            last = k15[-1]
            rng  = float(last.high - last.low)
            body = abs(float(last.close - last.open))
            br   = body / rng if rng > 0 else 0
            if   br >= 0.6: q_pts = 4
            elif br >= 0.4: q_pts = 2
            else:            q_pts = 0
            details["m15_body_ratio"] = round(br, 2)
            details["m15_quality_pts"] = q_pts
            pts += q_pts

    return min(30, pts), details


def _ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    if not values:
        return Decimal("0")
    k = Decimal(2) / Decimal(period + 1)
    result = values[0]
    for v in values[1:]:
        result = v * k + result * (1 - k)
    return result


def _ema_from(klines, period: int) -> Decimal:
    return _ema(tuple(k.close for k in klines), period)


def _atr(klines, period: int = 14) -> Decimal:
    if len(klines) < period + 1:
        return Decimal("0")
    trs = [
        max(cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close))
        for prev, cur in zip(klines[-(period + 1):-1], klines[-period:])
    ]
    return sum(trs) / Decimal(period)


def _trend_score(candidate, klines: dict | None, direction: str) -> tuple[int, dict]:
    """0–25 pts.  D1(5)+H4(8)+H1(8)+M15(4)."""
    details: dict = {}
    pts = 0

    if not klines:
        # Fallback using candidate data: market_regime + M15 EMA
        regime = getattr(candidate, "market_regime", "") or ""
        aligned_regime = (
            (direction == "LONG"  and "BULL" in regime) or
            (direction == "SHORT" and "BEAR" in regime)
        )
        base = 14 if aligned_regime else 8
        ema20 = getattr(candidate, "ema20", None)
        ema60 = getattr(candidate, "ema60", None)
        if ema20 and ema60:
            ema_ok = (direction == "LONG" and ema20 > ema60) or (direction == "SHORT" and ema20 < ema60)
            base += 4 if ema_ok else 0
        details["fallback_regime"] = regime
        return min(25, base), details

    # ── D1 EMA50 alignment (0–5) ───────────────────────────────────────────────
    k1d = klines.get("1d", ())
    if len(k1d) >= 52:
        ema50_1d  = _ema_from(k1d, 50)
        price_1d  = k1d[-1].close
        aligned_1d = (direction == "LONG" and price_1d > ema50_1d) or \
                     (direction == "SHORT" and price_1d < ema50_1d)
        d1_pts = 5 if aligned_1d else 0
        pts += d1_pts
        details["d1_ema50_aligned"] = aligned_1d
        details["d1_pts"] = d1_pts

    # ── H4 triple EMA (0–8) ───────────────────────────────────────────────────
    k4h = klines.get("4h", ())
    if len(k4h) >= 52:
        ema10_4h  = _ema_from(k4h, 10)
        ema20_4h  = _ema_from(k4h, 20)
        ema50_4h  = _ema_from(k4h, 50)
        price_4h  = k4h[-1].close
        if direction == "LONG":
            triple = ema10_4h > ema20_4h > ema50_4h
            aligned = price_4h > ema20_4h
        else:
            triple  = ema10_4h < ema20_4h < ema50_4h
            aligned = price_4h < ema20_4h
        h4_pts = 8 if triple else (5 if aligned else 2)
        pts += h4_pts
        details["h4_triple_ema"] = triple
        details["h4_ema_aligned"] = aligned
        details["h4_pts"] = h4_pts

    # ── H1 structure (0–8) ────────────────────────────────────────────────────
    k1h = klines.get("1h", ())
    if len(k1h) >= 20:
        from binance_ai_trader.classic.indicators import has_higher_low, has_lower_high
        ema20_1h = _ema_from(k1h, 20)
        price_1h = k1h[-1].close
        if direction == "LONG":
            hl = has_higher_low(k1h)
            pa = price_1h > ema20_1h
            h1_pts = 8 if hl else (4 if pa else 1)
        else:
            lh = has_lower_high(k1h)
            pa = price_1h < ema20_1h
            h1_pts = 8 if lh else (4 if pa else 1)
        pts += h1_pts
        details["h1_structure_pts"] = h1_pts

    # ── M15 trend bar (0–4) ───────────────────────────────────────────────────
    k15 = klines.get("15m", ())
    if len(k15) >= 20:
        ema20_15m = _ema_from(k15, 20)
        last15    = k15[-1]
        price_15m = last15.close
        bullish   = last15.close > last15.open
        if direction == "LONG":
            m15_pts = 4 if (bullish and price_15m > ema20_15m) else (2 if price_15m > ema20_15m else 0)
        else:
            bearish = last15.close < last15.open
            m15_pts = 4 if (bearish and price_15m < ema20_15m) else (2 if price_15m < ema20_15m else 0)
        pts += m15_pts
        details["m15_trend_pts"] = m15_pts

    return min(25, pts), details


def _entry_pos_score(candidate, klines: dict | None, direction: str) -> tuple[int, dict]:
    """0–20 pts.  H1(8)+M15(12). Measures distance from recent structure high/low."""
    details: dict = {}
    pts = 0

    if not klines or ("1h" not in klines and "15m" not in klines):
        # Fallback: |change_24h| as proxy for how far price has already moved
        ch24 = abs(getattr(candidate, "change_24h", 0) or 0)
        if   ch24 < 5:  fb = 14
        elif ch24 < 15: fb = 12
        elif ch24 < 25: fb = 10
        elif ch24 < 40: fb = 7
        else:            fb = 4
        details["fallback_change_24h"] = round(ch24, 1)
        return min(20, fb), details

    # ── H1 position (0–8) ─────────────────────────────────────────────────────
    k1h = klines.get("1h", ())
    if len(k1h) >= 16:
        from binance_ai_trader.classic.indicators import struct_high, struct_low
        atr14_1h = _atr(k1h, 14)
        price    = k1h[-1].close
        if float(atr14_1h) > 0:
            if direction == "LONG":
                dist = float((struct_high(k1h, 10) - price) / atr14_1h)
            else:
                dist = float((price - struct_low(k1h, 10)) / atr14_1h)
            if   dist > 3: h1_pts = 8
            elif dist > 2: h1_pts = 6
            elif dist > 1: h1_pts = 4
            elif dist > 0: h1_pts = 2
            else:           h1_pts = 1
            pts += h1_pts
            details["h1_dist_from_struct_atr"] = round(dist, 2)
            details["h1_pos_pts"] = h1_pts

    # ── M15 position (0–12) ───────────────────────────────────────────────────
    k15 = klines.get("15m", ())
    if len(k15) >= 16:
        from binance_ai_trader.classic.indicators import struct_high, struct_low
        atr14_15m = _atr(k15, 14)
        price     = k15[-1].close
        if float(atr14_15m) > 0:
            if direction == "LONG":
                dist = float((struct_high(k15, 8) - price) / atr14_15m)
            else:
                dist = float((price - struct_low(k15, 8)) / atr14_15m)
            if   dist > 4:   m15_pts = 12
            elif dist > 3:   m15_pts = 10
            elif dist > 2:   m15_pts = 8
            elif dist > 1:   m15_pts = 6
            elif dist > 0.5: m15_pts = 4
            else:             m15_pts = 2
            pts += m15_pts
            details["m15_dist_from_struct_atr"] = round(dist, 2)
            details["m15_pos_pts"] = m15_pts

    return min(20, pts), details


def _rr_score(candidate) -> tuple[int, dict]:
    """0–15 pts from stop_pct and planned RR."""
    stop_pct = float(getattr(candidate, "stop_pct", 0) or 0)
    rr       = float(getattr(candidate, "rr", 0) or 0)
    pts      = rr_score_pts(stop_pct, rr)
    return pts, {"stop_pct": round(stop_pct, 2), "planned_rr": round(rr, 2)}


def _strategy_fit_score(candidate, klines: dict | None) -> tuple[int, dict]:
    """0–10 pts. Per-strategy adapter reads actual entry conditions."""
    sid       = candidate.strategy_id
    direction = candidate.direction
    ch24      = abs(getattr(candidate, "change_24h", 0) or 0)
    vr        = float(getattr(candidate, "volume_ratio", 0) or 0)
    rr        = float(getattr(candidate, "rr", 0) or 0)
    stop_pct  = float(getattr(candidate, "stop_pct", 0) or 0)
    ema20     = float(getattr(candidate, "ema20", 0) or 0)
    ema60     = float(getattr(candidate, "ema60", 0) or 0)
    details   = {"adapter": _adapter_name(sid)}
    pts = 0

    if "hotlist_momentum_v3" in sid or sid == "hotlist_momentum_v3":
        # V3: broad momentum — |change_24h|≥15%, vol, stop, EMA alignment
        pts += 3 if ch24 >= 15 else (2 if ch24 >= 10 else 1 if ch24 >= 5 else 0)
        pts += 3 if vr >= 2.0 else (2 if vr >= 1.5 else 1 if vr >= 1.2 else 0)
        pts += 2 if stop_pct <= 5 else 0
        ema_ok = (direction == "LONG" and ema20 > ema60) or (direction == "SHORT" and ema20 < ema60)
        pts += 2 if ema_ok else 0

    elif "hotlist_v66" in sid:
        # V66: no trend filter — rely on hot watchlist + good stop/rr
        pts += 2 if ch24 >= 5 else 1 if ch24 >= 2 else 0
        pts += 3 if vr >= 1.5 else (2 if vr >= 1.2 else 1 if vr >= 1.0 else 0)
        pts += 3 if rr >= 2.0 else 2
        pts += 2 if stop_pct <= 5 else 1 if stop_pct <= 8 else 0

    elif "hotlist_v662" in sid:
        # V662: trend_aligned + vol≥1.2x + |move|≥5%
        pts += 2 if ch24 >= 5 else 1 if ch24 >= 3 else 0
        pts += 3 if vr >= 1.2 else 1 if vr >= 1.0 else 0
        pts += 3   # signal passed trend_aligned filter (both 1h+4h)
        pts += 2 if stop_pct <= 3 else 1 if stop_pct <= 4 else 0

    elif "hotlist_v663" in sid:
        # V663: triple_ema + vol≥1.2x (stricter trend)
        pts += 2 if ch24 >= 5 else 1 if ch24 >= 3 else 0
        pts += 3 if vr >= 1.5 else (2 if vr >= 1.2 else 1 if vr >= 1.0 else 0)
        pts += 3   # signal passed triple_ema filter (more selective than V662)
        pts += 2 if stop_pct <= 3 else 1 if stop_pct <= 4 else 0

    elif "hotlist_v664" in sid:
        # V664: precision pullback to EMA20 + vol contraction
        entry = float(getattr(candidate, "entry", "0") or 0)
        if ema20 > 0 and entry > 0:
            dist_pct = abs(entry - ema20) / entry * 100
            pts += 4 if dist_pct <= 0.3 else (3 if dist_pct <= 0.8 else 2 if dist_pct <= 1.5 else 1)
            details["ema20_dist_pct"] = round(dist_pct, 2)
        pts += 3 if vr < 0.8 else (2 if vr < 1.0 else 1 if vr < 1.2 else 0)
        pts += 3   # signal passed triple_ema + require_low_vol filter

    elif "wave_long" in sid:
        pts += 4 if vr >= 1.5 else (2 if vr >= 1.2 else 1)
        pts += 3 if ema20 > ema60 else 1
        pts += 3   # wave entry = retest at breakout level

    elif "wave_short" in sid:
        pts += 4 if vr >= 1.5 else (2 if vr >= 1.2 else 1)
        pts += 3 if ema20 < ema60 else 1
        pts += 3   # wave entry = breakdown retest

    elif "classic" in sid:
        # Classic strategies are handled via _unified_from_classic() in telegram_push.py.
        # If called here (e.g. via data_api), give a neutral middle score.
        pts = 5

    else:
        pts = 5   # unknown strategy — neutral

    return min(10, max(0, pts)), details


# ── comment generation ────────────────────────────────────────────────────────

_STRENGTH_COMMENTS = {
    "量能":     "量能充沛，资金关注度高",
    "趋势结构": "多周期趋势结构清晰",
    "入场位置": "入场时机较佳，空间合理",
    "风险收益": "风险收益比良好",
    "策略匹配": "信号符合策略核心条件",
}
_RISK_COMMENTS = {
    "量能":     "量能偏弱，注意成交额支撑",
    "趋势结构": "趋势支撑不足，需关注方向变化",
    "入场位置": "当前位置可能偏高/偏低，追入需谨慎",
    "风险收益": "前方压力支撑较近，真实空间有限",
    "策略匹配": "条件勉强达标，信号强度一般",
}

def _generate_summary(vol: int, trend: int, pos: int, rr: int, fit: int) -> str:
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
    if best[1] >= 0.7:
        lines.append(_STRENGTH_COMMENTS.get(best[0], ""))
    if worst[1] < 0.5:
        lines.append(_RISK_COMMENTS.get(worst[0], ""))
    return "\n".join(l for l in lines if l)


def _adapter_name(sid: str) -> str:
    if "hotlist_momentum_v3" in sid:  return "v3_momentum"
    if "hotlist_v664" in sid:          return "v664_pullback"
    if "hotlist_v663" in sid:          return "v663_triple_ema"
    if "hotlist_v662" in sid:          return "v662_trend_aligned"
    if "hotlist_v66"  in sid:          return "v66_hotlist"
    if "wave_long"    in sid:          return "wave_long_retest"
    if "wave_short"   in sid:          return "wave_short_retest"
    if "classic"      in sid:          return "classic_mapped"
    return "default"
