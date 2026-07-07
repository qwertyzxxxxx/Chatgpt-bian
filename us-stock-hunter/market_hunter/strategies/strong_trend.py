import pandas as pd
import logging
from market_hunter.config import STRONG_TREND_52W_DISTANCE, STRONG_TREND_PULLBACK_TOLERANCE

logger = logging.getLogger(__name__)


def check_strong_trend_pullback(df: pd.DataFrame) -> dict:
    """
    Strategy B: Strong Trend Pullback
    - MA20 > MA50 > MA200
    - Price above MA50
    - Price pulled back near MA20 or MA50
    - 52-week high distance <= 15%
    """
    result = {"triggered": False, "details": {}}

    required_cols = ["MA20", "MA50", "MA200", "High52W"]
    if df.empty or not all(c in df.columns for c in required_cols):
        return result

    df = df.dropna(subset=required_cols)
    if len(df) < 5:
        return result

    last = df.iloc[-1]
    close = last["Close"]
    ma20 = last["MA20"]
    ma50 = last["MA50"]
    ma200 = last["MA200"]
    high52 = last["High52W"]

    # Trend alignment
    if not (ma20 > ma50 > ma200):
        return result

    # Price above MA50
    if close < ma50:
        return result

    # 52-week high distance
    dist_52w = (high52 - close) / high52
    if dist_52w > STRONG_TREND_52W_DISTANCE:
        return result

    # Pulled back near MA20 or MA50
    pct_from_ma20 = (close - ma20) / ma20
    pct_from_ma50 = (close - ma50) / ma50

    near_ma20 = abs(pct_from_ma20) <= STRONG_TREND_PULLBACK_TOLERANCE
    near_ma50 = abs(pct_from_ma50) <= STRONG_TREND_PULLBACK_TOLERANCE

    if not (near_ma20 or near_ma50):
        return result

    result["triggered"] = True
    result["details"] = {
        "ma20": round(ma20, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "close": round(close, 2),
        "pct_from_ma20": round(pct_from_ma20 * 100, 2),
        "pct_from_ma50": round(pct_from_ma50 * 100, 2),
        "dist_52w_pct": round(dist_52w * 100, 2),
        "near_ma20": near_ma20,
        "near_ma50": near_ma50,
    }
    return result
