import logging
import yfinance as yf
import pandas as pd
from market_hunter.config import LOOKBACK_DAYS

logger = logging.getLogger(__name__)

PRICE_VALIDATION_TOLERANCE = 0.03  # 3%


def _yf_symbol(symbol: str) -> str:
    """
    Normalise a ticker for yfinance.
    S&P 500 CSV uses dots (BRK.B, BF.B); yfinance expects dashes (BRK-B, BF-B).
    """
    return symbol.replace(".", "-")


def _flatten_if_multiindex(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Safely extract OHLCV columns whether the DataFrame has flat or MultiIndex columns.
    - Flat columns: returned by Ticker.history() for a single symbol (normal path).
    - MultiIndex (metric, ticker): returned by yf.download() for multiple tickers.
    Handles both so callers are insulated from yfinance version differences.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    yfkey = _yf_symbol(symbol)
    # MultiIndex shape: level-0 = metric (Close/Open/…), level-1 = ticker
    for key in (yfkey, symbol):
        try:
            return df.xs(key, axis=1, level=1)
        except KeyError:
            pass
    # Fallback: try extracting by level-0 (single-ticker download edge case)
    try:
        return df.xs(symbol, axis=1, level=0)
    except KeyError:
        logger.warning(f"{symbol}: could not extract from MultiIndex columns")
        return pd.DataFrame()


def _clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Select and clean the standard OHLCV columns."""
    required = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    if len(required) < 5:
        return pd.DataFrame()
    df = df[required].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.dropna(inplace=True)
    return df


def validate_price(symbol: str, df: pd.DataFrame,
                   tolerance: float = PRICE_VALIDATION_TOLERANCE) -> bool:
    """
    Cross-check the latest close in `df` (300-day history) against a fresh
    5-day history fetch from yfinance.  Returns False and logs a warning
    when the difference exceeds `tolerance` (default 3%), signalling that
    data may be misaligned (stale cache, split not yet adjusted, etc.).

    On any network or parsing error, returns True (do not reject on doubt).
    """
    try:
        ticker = yf.Ticker(_yf_symbol(symbol))
        recent = ticker.history(period="5d", auto_adjust=True)
        if recent.empty:
            return True  # Cannot verify; assume OK
        recent = _flatten_if_multiindex(recent, symbol)
        if recent.empty or "Close" not in recent.columns:
            return True

        ref_close = float(recent["Close"].dropna().iloc[-1])
        hist_close = float(df["Close"].iloc[-1])

        if ref_close <= 0 or hist_close <= 0:
            logger.warning(f"{symbol}: price validation — non-positive close "
                           f"(hist={hist_close}, 5d={ref_close})")
            return False

        diff = abs(hist_close - ref_close) / ref_close
        if diff > tolerance:
            logger.warning(
                f"{symbol}: price mismatch — history_close={hist_close:.2f}, "
                f"5d_close={ref_close:.2f}, diff={diff:.1%} > {tolerance:.0%} — rejected"
            )
            return False

        return True

    except Exception as e:
        logger.warning(f"{symbol}: price validation error ({e}) — accepted anyway")
        return True


def get_ohlcv(symbol: str, period_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """Fetch daily OHLCV data from yfinance (single-ticker path)."""
    try:
        ticker = yf.Ticker(_yf_symbol(symbol))
        df = ticker.history(period=f"{period_days}d", auto_adjust=True)
        if df.empty:
            logger.warning(f"No data for {symbol}")
            return pd.DataFrame()
        df = _flatten_if_multiindex(df, symbol)
        return _clean_ohlcv(df)
    except Exception as e:
        logger.error(f"yfinance error for {symbol}: {e}")
        return pd.DataFrame()


def get_ohlcv_and_market_cap(
    symbol: str,
    period_days: int = LOOKBACK_DAYS,
    validate: bool = True,
) -> tuple[pd.DataFrame, float, bool]:
    """
    Fetch OHLCV + market cap in a single Ticker session.

    Returns (df, market_cap_usd, price_valid).
    - price_valid is False when the cross-check detects a >3% discrepancy.
    - market_cap is 0.0 when fast_info is unavailable.
    """
    try:
        ticker = yf.Ticker(_yf_symbol(symbol))

        df = ticker.history(period=f"{period_days}d", auto_adjust=True)
        if df.empty:
            logger.warning(f"No price data for {symbol}")
            return pd.DataFrame(), 0.0, True

        df = _flatten_if_multiindex(df, symbol)
        df = _clean_ohlcv(df)
        if df.empty:
            return pd.DataFrame(), 0.0, True

        market_cap = 0.0
        try:
            fi = ticker.fast_info
            market_cap = float(fi.market_cap or 0)
        except Exception:
            pass

        price_valid = True
        if validate and not df.empty:
            price_valid = validate_price(symbol, df)

        return df, market_cap, price_valid

    except Exception as e:
        logger.error(f"yfinance error for {symbol}: {e}")
        return pd.DataFrame(), 0.0, True


def get_sector_industry(symbol: str) -> dict:
    """
    Fetch sector and industry from yfinance ticker.info.
    Only call this for a small set of triggered signals — the info endpoint
    is heavier than fast_info.
    """
    try:
        info = yf.Ticker(_yf_symbol(symbol)).info
        return {
            "sector": info.get("sector", ""),
            "industry": info.get("industryDisp") or info.get("industry", ""),
        }
    except Exception as e:
        logger.warning(f"Could not fetch sector/industry for {symbol}: {e}")
        return {"sector": "", "industry": ""}


def get_spy_ohlcv(period_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """Fetch SPY data for relative strength calculation."""
    return get_ohlcv("SPY", period_days)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add MA and volume indicators to OHLCV dataframe."""
    if df.empty or len(df) < 20:
        return df
    df = df.copy()
    for n in [20, 50, 60, 200]:
        if len(df) >= n:
            df[f"MA{n}"] = df["Close"].rolling(n).mean()
        else:
            df[f"MA{n}"] = None

    df["VolMA20"] = df["Volume"].rolling(20).mean()
    df["DollarVol"] = df["Close"] * df["Volume"]
    df["DollarVolMA20"] = df["DollarVol"].rolling(20).mean()

    high_52w = df["High"].rolling(min(252, len(df))).max()
    df["High52W"] = high_52w

    return df


def avg_dollar_volume(df: pd.DataFrame, days: int = 20) -> float:
    """Calculate average daily dollar volume over recent N days."""
    if df.empty or len(df) < days:
        return 0.0
    recent = df.tail(days)
    dollar_vol = (recent["Close"] * recent["Volume"]).mean()
    return float(dollar_vol)
