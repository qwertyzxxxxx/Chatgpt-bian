import pandas as pd
import logging
from market_hunter.config import MA60_PULLBACK_WINDOW, MA60_TOLERANCE

logger = logging.getLogger(__name__)


def check_ma60_reclaim_pullback(df: pd.DataFrame) -> dict:
    """
    Strategy A: MA60 Reclaim Pullback
    - Stock had a downtrend (price below MA60)
    - Price broke above MA60
    - Price pulled back near MA60 within 15 trading days
    - Close not badly below MA60 (within tolerance)
    """
    result = {"triggered": False, "details": {}}

    if df.empty or "MA60" not in df.columns or len(df) < 80:
        return result

    df = df.dropna(subset=["MA60"])
    if len(df) < 30:
        return result

    close = df["Close"]
    ma60 = df["MA60"]

    # Find the most recent MA60 reclaim (price crossed above MA60)
    recent = df.tail(MA60_PULLBACK_WINDOW + 30)
    cross_idx = None

    for i in range(len(recent) - 1, 0, -1):
        prev_below = recent["Close"].iloc[i - 1] < recent["MA60"].iloc[i - 1]
        curr_above = recent["Close"].iloc[i] >= recent["MA60"].iloc[i]
        if prev_below and curr_above:
            cross_idx = i
            break

    if cross_idx is None:
        return result

    # Check downtrend before the reclaim: price was below MA60 for several days
    pre_cross = recent.iloc[max(0, cross_idx - 10):cross_idx]
    if pre_cross.empty:
        return result
    days_below = (pre_cross["Close"] < pre_cross["MA60"]).sum()
    if days_below < 3:
        return result

    # Check pullback: current price is near MA60 (within tolerance)
    current_close = close.iloc[-1]
    current_ma60 = ma60.iloc[-1]

    pct_from_ma60 = (current_close - current_ma60) / current_ma60

    # Must be within window of the reclaim
    days_since_cross = len(recent) - 1 - cross_idx
    if days_since_cross > MA60_PULLBACK_WINDOW:
        return result

    # Price should have pulled back near MA60 after the breakout
    # Allow slightly below or above MA60
    if not (-MA60_TOLERANCE * 2 <= pct_from_ma60 <= 0.10):
        return result

    # Confirm pullback: price was higher after the cross then came back
    post_cross = recent.iloc[cross_idx:]
    if post_cross.empty or len(post_cross) < 2:
        return result

    peak_after = post_cross["Close"].max()
    if peak_after <= current_close * 1.01:
        return result

    result["triggered"] = True
    result["details"] = {
        "cross_days_ago": days_since_cross,
        "days_below_before_cross": int(days_below),
        "pct_from_ma60": round(pct_from_ma60 * 100, 2),
        "peak_after_cross": round(peak_after, 2),
        "current_close": round(current_close, 2),
        "ma60": round(current_ma60, 2),
    }
    return result
