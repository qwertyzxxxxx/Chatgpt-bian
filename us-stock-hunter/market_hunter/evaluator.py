import logging
from datetime import datetime, date
import pandas as pd

from market_hunter.data.price import get_ohlcv
from market_hunter.database import db

logger = logging.getLogger(__name__)


def _get_trading_day_price(df: pd.DataFrame, signal_date: str, offset_days: int):
    """Get close price N trading days after signal date."""
    try:
        sig_dt = pd.Timestamp(signal_date)
        future = df[df.index > sig_dt]
        if len(future) < offset_days:
            return None, None
        target_row = future.iloc[offset_days - 1]
        return float(target_row["Close"]), future.index[offset_days - 1].strftime("%Y-%m-%d")
    except Exception:
        return None, None


def evaluate_signal(signal: dict) -> dict | None:
    """
    Evaluate a historical signal for 5/10/20 day returns, max drawdown, max gain.
    """
    symbol = signal["symbol"]
    signal_date = signal["signal_date"]
    signal_price = signal.get("close_price") or signal.get("signal_price")

    if not signal_price:
        return None

    df = get_ohlcv(symbol, period_days=365)
    if df.empty:
        return None

    sig_dt = pd.Timestamp(signal_date)
    future = df[df.index > sig_dt].copy()

    if future.empty:
        return None

    # 5/10/20 day returns
    price_5d, date_5d = _get_trading_day_price(df, signal_date, 5)
    price_10d, date_10d = _get_trading_day_price(df, signal_date, 10)
    price_20d, date_20d = _get_trading_day_price(df, signal_date, 20)

    ret_5d = (price_5d / signal_price - 1) * 100 if price_5d else None
    ret_10d = (price_10d / signal_price - 1) * 100 if price_10d else None
    ret_20d = (price_20d / signal_price - 1) * 100 if price_20d else None

    # Max drawdown and max gain in the 20-day window
    window = future.head(20)
    if not window.empty:
        highs = window["Close"].cummax()
        drawdowns = (window["Close"] - highs) / highs
        max_drawdown = float(drawdowns.min()) * 100

        max_gain = float((window["Close"].max() / signal_price - 1) * 100)
    else:
        max_drawdown = None
        max_gain = None

    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "signal_price": signal_price,
        "eval_date_5d": date_5d,
        "return_5d": round(ret_5d, 2) if ret_5d is not None else None,
        "eval_date_10d": date_10d,
        "return_10d": round(ret_10d, 2) if ret_10d is not None else None,
        "eval_date_20d": date_20d,
        "return_20d": round(ret_20d, 2) if ret_20d is not None else None,
        "max_drawdown": round(max_drawdown, 2) if max_drawdown is not None else None,
        "max_gain": round(max_gain, 2) if max_gain is not None else None,
    }


def run_evaluation() -> dict:
    """Evaluate all pending signals and store results."""
    db.init_db()
    pending = db.get_unevaluated_signals(days_old_min=5)
    logger.info(f"Found {len(pending)} signals to evaluate")

    evaluated = 0
    skipped = 0

    for signal in pending:
        try:
            result = evaluate_signal(signal)
            if result:
                db.insert_evaluation(signal["id"], result)
                evaluated += 1
                logger.info(
                    f"Evaluated {signal['symbol']} ({signal['signal_date']}): "
                    f"5d={result.get('return_5d')}% 10d={result.get('return_10d')}% "
                    f"20d={result.get('return_20d')}%"
                )
            else:
                skipped += 1
        except Exception as e:
            logger.error(f"Error evaluating {signal.get('symbol')}: {e}")
            skipped += 1

    return {
        "evaluated": evaluated,
        "skipped": skipped,
        "total": len(pending),
    }
