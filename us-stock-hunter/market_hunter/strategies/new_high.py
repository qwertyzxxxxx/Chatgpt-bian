import pandas as pd
import logging
from market_hunter.config import NEW_HIGH_VOLUME_MULTIPLIER

logger = logging.getLogger(__name__)


def check_new_high_breakout(df: pd.DataFrame) -> dict:
    """
    Strategy C: New High Breakout
    - Price breaks 52-week high
    - Volume >= 1.5x 20-day average volume
    - Price above MA20 and MA50
    """
    result = {"triggered": False, "details": {}}

    required_cols = ["MA20", "MA50", "VolMA20", "High52W"]
    if df.empty or not all(c in df.columns for c in required_cols):
        return result

    if len(df) < 22:
        return result

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = last["Close"]
    high = last["High"]
    volume = last["Volume"]
    ma20 = last["MA20"]
    ma50 = last["MA50"]
    vol_ma20 = last["VolMA20"]

    # Previous 52-week high (excluding today)
    prev_high52 = df["High"].iloc[:-1].rolling(min(252, len(df) - 1)).max().iloc[-1]

    if pd.isna(prev_high52) or pd.isna(vol_ma20) or vol_ma20 == 0:
        return result

    # Price breaks 52-week high today
    if not (high > prev_high52 and close > prev_high52 * 0.99):
        return result

    # Volume confirmation
    vol_ratio = volume / vol_ma20
    if vol_ratio < NEW_HIGH_VOLUME_MULTIPLIER:
        return result

    # Price above MA20 and MA50
    if pd.isna(ma20) or pd.isna(ma50):
        return result
    if close < ma20 or close < ma50:
        return result

    result["triggered"] = True
    result["details"] = {
        "close": round(close, 2),
        "prev_52w_high": round(prev_high52, 2),
        "pct_above_52w_high": round((close / prev_high52 - 1) * 100, 2),
        "volume": int(volume),
        "vol_ma20": int(vol_ma20),
        "vol_ratio": round(vol_ratio, 2),
        "ma20": round(ma20, 2),
        "ma50": round(ma50, 2),
    }
    return result
