"""
Entry Engine — rule-based entry/exit level computation per strategy.

No AI, no prediction. All levels derived from OHLCV + indicators.

Two-stage design:
  1. compute_entry_plan()   — price levels only (entry zone, trigger, stop, targets, RR)
  2. compute_trade_readiness() — final action_status using full context (price, vol, score)
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)

MAX_RISK_PCT  = 8.0    # risk_pct above this → "风险过高，等待回踩"
MIN_RR_RATIO  = 1.8    # rr below this → "观察"
MIN_VOL_RATIO = 1.2    # volume_ratio below this → "观察"
MIN_SCORE     = 70.0   # total_score below this → "观察"


def _r2(v) -> float:
    return round(float(v), 2)


def _rr(reward: float, risk: float) -> float | None:
    if risk <= 0:
        return None
    return round(reward / risk, 1)


# ---------------------------------------------------------------------------
# Stage 1 — compute price levels
# ---------------------------------------------------------------------------

def compute_entry_plan(strategy_name: str, df: pd.DataFrame, details: dict) -> dict:
    """
    Compute entry zone, trigger, stop, targets and raw RR/risk.
    Does NOT set action_status — call compute_trade_readiness() for that.
    Returns {} on any error.
    """
    try:
        if df.empty or len(df) < 2:
            return {}
        last = df.iloc[-1]
        close = float(last["Close"])

        if strategy_name == "ma60_reclaim":
            return _plan_ma60(last, df, close, details)
        if strategy_name == "strong_trend":
            return _plan_strong_trend(last, df, close, details)
        if strategy_name == "new_high":
            return _plan_new_high(last, df, close, details)
        return {}
    except Exception as e:
        logger.debug(f"entry_engine error ({strategy_name}): {e}")
        return {}


# ---------------------------------------------------------------------------
# Stage 2 — trade readiness (action_status from full context)
# ---------------------------------------------------------------------------

def compute_trade_readiness(
    ep: dict,
    close_price: float,
    vol_ratio: float | None,
    total_score: float,
    strategy_name: str,
) -> dict:
    """
    Determine final action_status + action_reason using ALL context.
    Modifies ep in-place and returns it.

    Rules (in priority order):
      ① Price > Entry Zone High × 1.02 → "观察，等待回踩"
      ② Price < Entry Zone Low         → "观察，等待进入买入区"
      ③ All pass (price/RR/risk/vol/score) → "可关注买点"
      ④ Primary failure reason        → "观察" or "风险过高，等待回踩"
    """
    if not ep:
        return ep

    ez_low  = ep.get("entry_zone_low")
    ez_high = ep.get("entry_zone_high")
    trigger = ep.get("trigger_price")
    rr      = ep.get("rr_ratio")
    risk    = ep.get("risk_pct")

    if ez_low is None or ez_high is None:
        ep["action_status"] = "观察"
        ep["action_reason"] = "入场区间未计算"
        return ep

    # Rule ①
    if close_price > float(ez_high) * 1.02:
        ep["action_status"] = "观察，等待回踩"
        ep["action_reason"] = "当前价格已明显高于买入区"
        return ep

    # Rule ②
    if close_price < float(ez_low):
        ep["action_status"] = "观察，等待进入买入区"
        ep["action_reason"] = "当前价格尚未进入买入区"
        return ep

    # Rule ③ — all conditions
    price_ok = (float(ez_low) <= close_price <= float(ez_high)) or (
        trigger is not None and close_price <= float(trigger) * 1.01
    )
    rr_ok    = rr is not None and float(rr) >= MIN_RR_RATIO
    risk_ok  = risk is not None and float(risk) <= MAX_RISK_PCT
    vol_ok   = vol_ratio is not None and float(vol_ratio) >= MIN_VOL_RATIO
    score_ok = total_score >= MIN_SCORE

    if price_ok and rr_ok and risk_ok and vol_ok and score_ok:
        ep["action_status"] = "可关注买点"
        ep["action_reason"] = ""
        return ep

    # Rule ④ — explain primary failure
    if risk is not None and float(risk) > MAX_RISK_PCT:
        ep["action_status"] = "风险过高，等待回踩"
        ep["action_reason"] = f"当前风险 {float(risk):.1f}%，超过 {MAX_RISK_PCT:.0f}% 上限"
    elif not rr_ok:
        rr_txt = f"1:{float(rr):.1f}" if rr is not None else "未计算"
        ep["action_status"] = "观察"
        ep["action_reason"] = f"风险回报比不足（{rr_txt}）"
    elif not vol_ok:
        vr_txt = f"{float(vol_ratio):.1f}x" if vol_ratio is not None else "未知"
        ep["action_status"] = "观察"
        ep["action_reason"] = f"成交量不足（量比 {vr_txt} < {MIN_VOL_RATIO:.1f}x）"
    elif not score_ok:
        ep["action_status"] = "观察"
        ep["action_reason"] = f"评分不足（{total_score:.0f} < {MIN_SCORE:.0f}）"
    else:
        ep["action_status"] = "观察"
        ep["action_reason"] = "价格不在触发区间"

    return ep


# ---------------------------------------------------------------------------
# Strategy A — MA60 Reclaim Pullback
# ---------------------------------------------------------------------------

def _plan_ma60(last, df: pd.DataFrame, close: float, details: dict) -> dict:
    ma60   = float(last.get("MA60") or details.get("ma60") or close)
    high52 = float(last.get("High52W") or close * 1.25)

    entry_low  = _r2(ma60 * 0.98)
    entry_high = _r2(ma60 * 1.02)
    trigger    = _r2(ma60 * 1.005)
    stop       = _r2(ma60 * 0.97)

    swing_high = float(details.get("peak_after_cross") or df["High"].tail(30).max())
    target1    = _r2(max(swing_high, trigger * 1.08))
    target2    = _r2(high52)

    risk    = max(trigger - stop, 0.01)
    reward1 = max(target1 - trigger, 0)
    risk_pct = _r2((risk / trigger) * 100) if trigger > 0 else None
    rr       = _rr(reward1, risk)

    return {
        "entry_zone_low":  entry_low,
        "entry_zone_high": entry_high,
        "trigger_price":   trigger,
        "stop_loss":       stop,
        "risk_pct":        risk_pct,
        "target1":         target1,
        "target2":         target2,
        "target2_note":    None,
        "rr_ratio":        rr,
        "action_status":   None,
        "action_reason":   "",
    }


# ---------------------------------------------------------------------------
# Strategy B — Strong Trend Pullback
# ---------------------------------------------------------------------------

def _plan_strong_trend(last, df: pd.DataFrame, close: float, details: dict) -> dict:
    ma20     = float(last.get("MA20") or details.get("ma20") or close)
    ma50     = float(last.get("MA50") or details.get("ma50") or close)
    near_ma20 = details.get("near_ma20", True)

    anchor     = ma20 if near_ma20 else ma50
    entry_low  = _r2(anchor * 0.98)
    entry_high = _r2(anchor * 1.02)
    trigger    = _r2(float(df["High"].iloc[-2]))

    # Stop: closer of (recent swing low −1%) vs (MA50 −2%)
    recent_swing_low = float(df["Low"].tail(5).min())
    stop = _r2(max(recent_swing_low * 0.99, ma50 * 0.98))

    target1 = _r2(close * 1.10)

    risk    = max(trigger - stop, 0.01)
    reward1 = max(target1 - trigger, 0)
    risk_pct = _r2((risk / trigger) * 100) if trigger > 0 else None
    rr       = _rr(reward1, risk)

    return {
        "entry_zone_low":  entry_low,
        "entry_zone_high": entry_high,
        "trigger_price":   trigger,
        "stop_loss":       stop,
        "risk_pct":        risk_pct,
        "target1":         target1,
        "target2":         None,
        "target2_note":    "跟踪止盈（MA20）",
        "rr_ratio":        rr,
        "action_status":   None,
        "action_reason":   "",
    }


# ---------------------------------------------------------------------------
# Strategy C — 52-Week High Breakout
# ---------------------------------------------------------------------------

def _plan_new_high(last, df: pd.DataFrame, close: float, details: dict) -> dict:
    prev_high = float(details.get("prev_52w_high") or close * 0.98)

    entry_low  = _r2(prev_high)
    entry_high = _r2(prev_high * 1.05)

    # Rule ④: trigger must never be lower than entry_zone_low (prev_high)
    trigger = _r2(max(close, prev_high))
    stop    = _r2(prev_high * 0.935)
    target1 = _r2(trigger * 1.15)

    risk    = max(trigger - stop, 0.01)
    reward1 = max(target1 - trigger, 0)
    risk_pct = _r2((risk / trigger) * 100) if trigger > 0 else None
    rr       = _rr(reward1, risk)

    return {
        "entry_zone_low":  entry_low,
        "entry_zone_high": entry_high,
        "trigger_price":   trigger,
        "stop_loss":       stop,
        "risk_pct":        risk_pct,
        "target1":         target1,
        "target2":         None,
        "target2_note":    "跟踪止盈",
        "rr_ratio":        rr,
        "action_status":   None,
        "action_reason":   "",
    }
