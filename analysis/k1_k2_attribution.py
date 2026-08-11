#!/usr/bin/env python3
"""
K1 / K2 TP/SL Attribution Analysis
===================================
READ-ONLY: no DB writes, no production code changes.

Methodology:
  For each settled K1/K2 trade, fetch historical klines from Binance Futures
  (15m / 1H / 4H / 1D) ending at filled_at (= signal entry candle close).
  Compute 20 features per trade, then bucket-analyse which features
  separate TP from SL.
"""
from __future__ import annotations

import sys
import os
import statistics
import time
import math
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Trade data (pulled directly from production DB via executeSql)
# filled_at = actual entry time (first 15m bar close after signal)
# closed_at = settlement time
# ─────────────────────────────────────────────────────────────────────────────
TRADES_RAW = [
    # ── K1 ────────────────────────────────────────────────────────────────────
    {
        "strategy_id": "classic_k1",
        "order_id":    "6c505821",
        "symbol":      "TAOUSDT",
        "direction":   "LONG",
        "entry":       197.38,
        "sl":          194.3598571,
        "tp1":         203.4202857,
        "rr":          2.0,
        "result":      "TP1",
        "pnl_pct":     3.06,
        "rr_realized": 2.00,
        "filled_at":   "2026-08-08T06:45:00+00:00",
        "closed_at":   "2026-08-09T00:02:10+00:00",
        # DB scan-record features (NULL = not linked)
        "db": {},
    },
    {
        "strategy_id": "classic_k1",
        "order_id":    "aa8a0f8a",
        "symbol":      "CAPUSDT",
        "direction":   "LONG",
        "entry":       0.03766,
        "sl":          0.03635414,
        "tp1":         0.03961879,
        "rr":          1.5,
        "result":      "SL",
        "pnl_pct":     -3.47,
        "rr_realized": -1.00,
        "filled_at":   "2026-08-08T13:00:00+00:00",
        "closed_at":   "2026-08-08T13:22:35+00:00",
        "db": {},
    },
    {
        "strategy_id": "classic_k1",
        "order_id":    "7f9f04e3",
        "symbol":      "DODOXUSDT",
        "direction":   "LONG",
        "entry":       0.022382,
        "sl":          0.021368,
        "tp1":         0.024411,
        "rr":          2.0,
        "result":      "SL",
        "pnl_pct":     -4.53,
        "rr_realized": -1.00,
        "filled_at":   "2026-08-08T21:15:00+00:00",
        "closed_at":   "2026-08-09T01:03:03+00:00",
        "db": {},
    },
    {
        "strategy_id": "classic_k1",
        "order_id":    "b1fc647b",
        "symbol":      "BANANAS31USDT",
        "direction":   "LONG",
        "entry":       0.008793,
        "sl":          0.008388,
        "tp1":         0.009603,
        "rr":          2.0,
        "result":      "TP1",
        "pnl_pct":     9.21,
        "rr_realized": 2.00,
        "filled_at":   "2026-08-10T14:00:00+00:00",
        "closed_at":   "2026-08-11T06:39:23+00:00",
        "db": {
            "change_24h":   4.953,
            "change_3d":    9.346,
            "change_7d":    12.099,
            "range_pos_30d":0.580,
            "consec_days":  2,
            "trend_4h":     "BULL",
            "atr_dist_4h":  0.777,
            "vol_ratio_1h": 0.334,
            "vol_ratio_15m":2.196,
            "vol_grade":    "S",
            "pool_rank":    19,
            "score":        92,
        },
    },
    {
        "strategy_id": "classic_k1",
        "order_id":    "64ed3c90",
        "symbol":      "LINKUSDT",
        "direction":   "LONG",
        "entry":       8.414,
        "sl":          8.35730,
        "tp1":         8.52740,
        "rr":          2.0,
        "result":      "TP1",
        "pnl_pct":     1.35,
        "rr_realized": 2.00,
        "filled_at":   "2026-08-11T06:45:00+00:00",
        "closed_at":   "2026-08-11T09:28:19+00:00",
        "db": {
            "change_24h":    2.333,
            "change_3d":    -0.134,
            "change_7d":    -2.363,
            "range_pos_30d": 0.347,
            "consec_days":   1,
            "trend_4h":      "BULL",
            "atr_dist_4h":   1.602,
            "vol_ratio_1h":  0.780,
            "vol_ratio_15m": 2.205,
            "vol_grade":     "S",
            "pool_rank":     14,
            "score":         78,
        },
    },
    {
        "strategy_id": "classic_k1",
        "order_id":    "4cd64741",
        "symbol":      "ICPUSDT",
        "direction":   "LONG",
        "entry":       2.307,
        "sl":          2.268729,
        "tp1":         2.364407,
        "rr":          1.5,
        "result":      "SL",
        "pnl_pct":     -1.66,
        "rr_realized": -1.00,
        "filled_at":   "2026-08-11T14:00:00+00:00",
        "closed_at":   "2026-08-11T15:49:50+00:00",
        "db": {
            "change_24h":    3.659,
            "change_3d":     3.438,
            "change_7d":     4.436,
            "range_pos_30d": 0.529,
            "consec_days":   1,
            "trend_4h":      "BULL",
            "atr_dist_4h":   1.551,
            "vol_ratio_1h":  0.496,
            "vol_ratio_15m": 1.346,
            "vol_grade":     "NORMAL",
            "pool_rank":     11,
            "score":         79,
        },
    },
    # ── K2 ────────────────────────────────────────────────────────────────────
    {
        "strategy_id": "classic_k2",
        "order_id":    "cafc2822",
        "symbol":      "CYSUSDT",
        "direction":   "LONG",
        "entry":       0.9615,
        "sl":          0.911194,
        "tp1":         1.062111,
        "rr":          2.0,
        "result":      "TP1",
        "pnl_pct":     10.46,
        "rr_realized": 2.00,
        "filled_at":   "2026-08-08T03:00:00+00:00",
        "closed_at":   "2026-08-08T06:30:21+00:00",
        "db": {},
    },
    {
        "strategy_id": "classic_k2",
        "order_id":    "8591728a",
        "symbol":      "MMTUSDT",
        "direction":   "LONG",
        "entry":       0.2076,
        "sl":          0.200094,
        "tp1":         0.222611,
        "rr":          2.0,
        "result":      "SL",
        "pnl_pct":     -3.62,
        "rr_realized": -1.00,
        "filled_at":   "2026-08-10T02:15:00+00:00",
        "closed_at":   "2026-08-10T05:27:41+00:00",
        "db": {
            "change_24h":    6.371,
            "change_3d":    25.638,
            "change_7d":     7.709,
            "range_pos_30d": 0.180,
            "consec_days":   4,
            "trend_4h":      "BULL",
            "atr_dist_4h":   0.362,
            "vol_ratio_1h":  0.580,
            "vol_ratio_15m": 4.810,
            "vol_grade":     "S_PLUS",
            "pool_rank":     15,
            "score":         85,
        },
    },
]

BASE_URL = "https://fapi.binance.com"


# ─────────────────────────────────────────────────────────────────────────────
# Binance REST helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    return int(dt.timestamp() * 1000)

def _fetch_klines(
    symbol: str,
    interval: str,
    end_ms: int,
    limit: int = 120,
) -> list[dict]:
    """Fetch closed klines ending at end_ms. Returns list of dicts."""
    r = requests.get(
        f"{BASE_URL}/fapi/v1/klines",
        params={
            "symbol":    symbol,
            "interval":  interval,
            "endTime":   str(end_ms),
            "limit":     str(limit),
        },
        timeout=15,
    )
    r.raise_for_status()
    raw = r.json()
    # Each element: [open_time, open, high, low, close, volume, close_time, quote_vol, ...]
    return [
        {
            "open_time": k[0],
            "open":      float(k[1]),
            "high":      float(k[2]),
            "low":       float(k[3]),
            "close":     float(k[4]),
            "volume":    float(k[5]),
            "quote_vol": float(k[7]),
            "close_time":k[6],
        }
        for k in raw
    ]

def _fetch_klines_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Fetch klines between start_ms and end_ms (for MFE/MAE)."""
    r = requests.get(
        f"{BASE_URL}/fapi/v1/klines",
        params={
            "symbol":    symbol,
            "interval":  interval,
            "startTime": str(start_ms),
            "endTime":   str(end_ms),
            "limit":     "500",
        },
        timeout=15,
    )
    r.raise_for_status()
    raw = r.json()
    return [
        {
            "open_time": k[0],
            "open":      float(k[1]),
            "high":      float(k[2]),
            "low":       float(k[3]),
            "close":     float(k[4]),
            "volume":    float(k[5]),
            "quote_vol": float(k[7]),
            "close_time":k[6],
        }
        for k in raw
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Indicator functions (pure Python, no pandas)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k = 2 / (period + 1)
    val = sum(closes[:period]) / period
    for c in closes[period:]:
        val = c * k + val * (1 - k)
    return val

def _ema_series(closes: list[float], period: int) -> list[float]:
    if len(closes) < period:
        return [closes[-1]] if closes else [0.0]
    k = 2 / (period + 1)
    vals = [sum(closes[:period]) / period]
    for c in closes[period:]:
        vals.append(c * k + vals[-1] * (1 - k))
    return vals

def _atr(klines: list[dict], period: int = 14) -> float:
    if len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i]["high"], klines[i]["low"], klines[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    val = sum(trs[:period]) / period
    for tr in trs[period:]:
        val = (val * (period - 1) + tr) / period
    return val

def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)

def _vol_ratio(klines: list[dict], lookback: int = 20) -> float:
    if len(klines) < lookback + 1:
        return 1.0
    prev_vols = [k["quote_vol"] for k in klines[-(lookback + 1):-1]]
    med = statistics.median(prev_vols)
    if med == 0:
        return 1.0
    return klines[-1]["quote_vol"] / med

def _range_pos_30d(klines_1d: list[dict]) -> float:
    """30-day range position: (close - min30) / (max30 - min30)."""
    if len(klines_1d) < 2:
        return 0.5
    recent = klines_1d[-30:] if len(klines_1d) >= 30 else klines_1d
    hi = max(k["high"] for k in recent)
    lo = min(k["low"] for k in recent)
    if hi == lo:
        return 0.5
    return (klines_1d[-1]["close"] - lo) / (hi - lo)

def _change_nd(klines_1d: list[dict], n: int) -> float:
    if len(klines_1d) < n + 1:
        return 0.0
    prev = klines_1d[-(n+1)]["close"]
    if prev == 0:
        return 0.0
    return (klines_1d[-1]["close"] - prev) / prev * 100

def _ema_slope_pct(closes: list[float], period: int, lookback: int = 5) -> float:
    """EMA slope: (ema_now - ema_prev) / ema_prev × 100."""
    if len(closes) < period + lookback:
        return 0.0
    ema_now  = _ema(closes, period)
    ema_prev = _ema(closes[:-lookback], period)
    if ema_prev == 0:
        return 0.0
    return (ema_now - ema_prev) / ema_prev * 100

def _swing_lows(klines: list[dict], window: int = 3) -> list[tuple[int,float]]:
    """Return [(index, price)] of local swing lows."""
    result = []
    n = len(klines)
    for i in range(window, n - window):
        lo = klines[i]["low"]
        if (all(klines[i-j]["low"] >= lo for j in range(1, window+1)) and
                all(klines[i+j]["low"] >= lo for j in range(1, window+1))):
            result.append((i, lo))
    return result

def _swing_highs(klines: list[dict], window: int = 3) -> list[tuple[int,float]]:
    """Return [(index, price)] of local swing highs."""
    result = []
    n = len(klines)
    for i in range(window, n - window):
        hi = klines[i]["high"]
        if (all(klines[i-j]["high"] <= hi for j in range(1, window+1)) and
                all(klines[i+j]["high"] <= hi for j in range(1, window+1))):
            result.append((i, hi))
    return result

def _has_higher_low(klines: list[dict]) -> bool:
    lows = _swing_lows(klines)
    return len(lows) >= 2 and lows[-1][1] > lows[-2][1]

def _fibonacci_position(entry: float, swing_lo: float, swing_hi: float) -> str:
    """Return Fibonacci retracement bucket."""
    if swing_hi == swing_lo:
        return "N/A"
    fib = (swing_hi - entry) / (swing_hi - swing_lo)  # 0=at high, 1=at low
    if fib < 0.214:    return ">0.786"       # deep pullback >78.6%
    elif fib < 0.382:  return "0.618-0.786"
    elif fib < 0.500:  return "0.500-0.618"
    elif fib < 0.618:  return "0.382-0.500"
    elif fib < 1.000:  return "<0.382"        # shallow pullback <38.2%
    else:              return ">0.786"

def _pullback_depth(klines_15m: list[dict], entry: float) -> float:
    """Pullback depth: how far price retraced from the recent swing high before entry.
    Returns positive pct value."""
    highs = _swing_highs(klines_15m[-30:], window=2)
    if not highs:
        return 0.0
    recent_sh = highs[-1][1]
    # Find min low between that swing high and entry
    sh_idx = klines_15m[-30:][highs[-1][0]]["close"]  # approx
    recent_lows = [k["low"] for k in klines_15m[-15:]]
    if not recent_lows:
        return 0.0
    pb_low = min(recent_lows)
    if recent_sh == 0:
        return 0.0
    return (recent_sh - pb_low) / recent_sh * 100

def _dist_to_swing(klines: list[dict], entry: float, direction: str) -> float:
    """Distance (%) from entry to nearest swing high (SHORT) or swing low (LONG)."""
    if direction == "LONG":
        lows = _swing_lows(klines[-20:], window=2)
        if not lows:
            return 0.0
        nearest = min(abs(entry - p) for _, p in lows)
    else:
        highs = _swing_highs(klines[-20:], window=2)
        if not highs:
            return 0.0
        nearest = min(abs(entry - p) for _, p in highs)
    return nearest / entry * 100 if entry else 0.0

def _mfe_mae(holding_klines: list[dict], entry: float, direction: str) -> tuple[float,float]:
    """MFE and MAE as positive % values."""
    if not holding_klines:
        return 0.0, 0.0
    if direction == "LONG":
        mfe = max((k["high"] - entry) / entry * 100 for k in holding_klines)
        mae = max((entry - k["low"]) / entry * 100 for k in holding_klines)
    else:
        mfe = max((entry - k["low"]) / entry * 100 for k in holding_klines)
        mae = max((k["high"] - entry) / entry * 100 for k in holding_klines)
    return max(mfe, 0.0), max(mae, 0.0)

def _signal_candle_change(k: dict) -> float:
    """Signal candle body change (close-open)/open %."""
    if k["open"] == 0:
        return 0.0
    return (k["close"] - k["open"]) / k["open"] * 100


# ─────────────────────────────────────────────────────────────────────────────
# Feature computation for one trade
# ─────────────────────────────────────────────────────────────────────────────

def compute_trade_features(trade: dict) -> dict:
    symbol      = trade["symbol"]
    direction   = trade["direction"]
    entry       = trade["entry"]
    filled_ms   = _to_ms(trade["filled_at"])
    closed_ms   = _to_ms(trade["closed_at"])

    print(f"  {symbol} {direction} filled={trade['filled_at'][:16]}", flush=True)

    features = dict(trade)  # copy all trade fields

    # ── Fetch klines at signal time ───────────────────────────────────────
    try:
        klines_1d  = _fetch_klines(symbol, "1d",  end_ms=filled_ms, limit=35)
        time.sleep(0.12)
        klines_4h  = _fetch_klines(symbol, "4h",  end_ms=filled_ms, limit=75)
        time.sleep(0.12)
        klines_1h  = _fetch_klines(symbol, "1h",  end_ms=filled_ms, limit=55)
        time.sleep(0.12)
        klines_15m = _fetch_klines(symbol, "15m", end_ms=filled_ms, limit=60)
        time.sleep(0.12)
        # Drop the last (partially-formed) candle
        klines_1d  = klines_1d[:-1]  if klines_1d  else []
        klines_4h  = klines_4h[:-1]  if klines_4h  else []
        klines_1h  = klines_1h[:-1]  if klines_1h  else []
        klines_15m = klines_15m[:-1] if klines_15m else []
    except Exception as e:
        print(f"    !! klines fetch failed: {e}")
        features["_klines_ok"] = False
        return features

    # ── MFE / MAE (holding period) ────────────────────────────────────────
    try:
        hold_klines = _fetch_klines_range(symbol, "15m", filled_ms, closed_ms)
        time.sleep(0.12)
        mfe, mae = _mfe_mae(hold_klines, entry, direction)
    except Exception as e:
        print(f"    !! MFE/MAE fetch failed: {e}")
        mfe, mae = 0.0, 0.0

    features["_klines_ok"] = True

    # ── 1. ret_24h / ret_3d / ret_7d ─────────────────────────────────────
    if features["db"].get("change_24h") is not None:
        features["ret_24h"] = features["db"]["change_24h"]
        features["ret_3d"]  = features["db"]["change_3d"]
        features["ret_7d"]  = features["db"]["change_7d"]
    else:
        features["ret_24h"] = _change_nd(klines_1d, 1)
        features["ret_3d"]  = _change_nd(klines_1d, 3)
        features["ret_7d"]  = _change_nd(klines_1d, 7)

    # ── 4. range_pos_30d ─────────────────────────────────────────────────
    if features["db"].get("range_pos_30d") is not None:
        features["range_pos_30d"] = features["db"]["range_pos_30d"]
    else:
        features["range_pos_30d"] = _range_pos_30d(klines_1d)

    # ── 5. distance_to_4h_ema20_atr ──────────────────────────────────────
    if features["db"].get("atr_dist_4h") is not None:
        features["atr_dist_4h"] = features["db"]["atr_dist_4h"]
    else:
        if len(klines_4h) >= 22:
            ema20_4h = _ema([k["close"] for k in klines_4h], 20)
            atr14_4h = _atr(klines_4h, 14)
            dist = abs(entry - ema20_4h)
            features["atr_dist_4h"] = dist / atr14_4h if atr14_4h > 0 else 0.0
        else:
            features["atr_dist_4h"] = None

    # ── 6. 4H EMA20 slope ────────────────────────────────────────────────
    if len(klines_4h) >= 25:
        features["slope_4h_ema20"] = _ema_slope_pct([k["close"] for k in klines_4h], 20)
    else:
        features["slope_4h_ema20"] = None

    # ── 7. 1H EMA20 slope ────────────────────────────────────────────────
    if len(klines_1h) >= 25:
        features["slope_1h_ema20"] = _ema_slope_pct([k["close"] for k in klines_1h], 20)
    else:
        features["slope_1h_ema20"] = None

    # ── 8. 1H market structure (has higher low) ───────────────────────────
    if len(klines_1h) >= 12:
        features["hl_1h"] = _has_higher_low(klines_1h)
    else:
        features["hl_1h"] = None

    # ── 9. 1H volume_ratio ────────────────────────────────────────────────
    if features["db"].get("vol_ratio_1h") is not None:
        features["vol_ratio_1h"] = features["db"]["vol_ratio_1h"]
    else:
        features["vol_ratio_1h"] = _vol_ratio(klines_1h) if len(klines_1h) >= 22 else None

    # ── 10. 15m volume_ratio ─────────────────────────────────────────────
    if features["db"].get("vol_ratio_15m") is not None:
        features["vol_ratio_15m"] = features["db"]["vol_ratio_15m"]
    else:
        features["vol_ratio_15m"] = _vol_ratio(klines_15m) if len(klines_15m) >= 22 else None

    # ── 11. signal candle price_change_pct ───────────────────────────────
    if klines_15m:
        features["sig_candle_chg_pct"] = _signal_candle_change(klines_15m[-1])
    else:
        features["sig_candle_chg_pct"] = None

    # ── 12. RSI14 (15m) ───────────────────────────────────────────────────
    if len(klines_15m) >= 16:
        closes_15m = [k["close"] for k in klines_15m]
        features["rsi14"] = _rsi(closes_15m)
    else:
        features["rsi14"] = None

    # ── 13. pullback_depth_pct ────────────────────────────────────────────
    if len(klines_15m) >= 15:
        features["pullback_depth"] = _pullback_depth(klines_15m, entry)
    else:
        features["pullback_depth"] = None

    # ── 14. Fibonacci retracement position ───────────────────────────────
    if len(klines_1h) >= 12:
        sl = _swing_lows(klines_1h[-30:], window=3)
        sh = _swing_highs(klines_1h[-30:], window=3)
        if sl and sh:
            recent_sl = sl[-1][1]
            recent_sh = sh[-1][1]
            features["fib_bucket"] = _fibonacci_position(entry, recent_sl, recent_sh)
            features["fib_raw"] = ((recent_sh - entry) / (recent_sh - recent_sl)
                                   if recent_sh != recent_sl else 0.5)
        else:
            features["fib_bucket"] = "N/A"
            features["fib_raw"] = None
    else:
        features["fib_bucket"] = "N/A"
        features["fib_raw"] = None

    # ── 15. distance to nearest swing high/low ────────────────────────────
    if len(klines_1h) >= 12:
        features["dist_to_swing_pct"] = _dist_to_swing(klines_1h[-30:], entry, direction)
    else:
        features["dist_to_swing_pct"] = None

    # ── 16-18. MFE / MAE / holding time ──────────────────────────────────
    features["mfe_pct"] = mfe
    features["mae_pct"] = mae
    dt_filled = datetime.fromisoformat(trade["filled_at"])
    dt_closed  = datetime.fromisoformat(trade["closed_at"])
    features["hold_hours"] = (dt_closed - dt_filled).total_seconds() / 3600

    # ── 19-20. PnL / RR (from DB) ────────────────────────────────────────
    features["actual_pnl_pct"]  = trade["pnl_pct"]
    features["actual_rr"]       = trade["rr_realized"]

    # Score (if available)
    features["score"] = features["db"].get("score")

    return features


# ─────────────────────────────────────────────────────────────────────────────
# Statistics helpers
# ─────────────────────────────────────────────────────────────────────────────

def _avg(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None

def _med(vals):
    v = [x for x in vals if x is not None]
    return statistics.median(v) if v else None

def _pct(num, den):
    return f"{num/den*100:.1f}%" if den else "N/A"

def _fmt(x, decimals=2):
    if x is None: return "  N/A"
    return f"{x:>{7}.{decimals}f}"


# ─────────────────────────────────────────────────────────────────────────────
# Attribution analysis
# ─────────────────────────────────────────────────────────────────────────────

def _split(trades, key):
    tp = [t for t in trades if t["result"] == "TP1" and t.get(key) is not None]
    sl = [t for t in trades if t["result"] == "SL"  and t.get(key) is not None]
    return tp, sl

def _win_rate(trades):
    if not trades: return 0.0
    return sum(1 for t in trades if t["result"] == "TP1") / len(trades)

def _pf(trades):
    """Profit factor = sum(profits) / sum(losses)."""
    wins  = sum(t["actual_pnl_pct"] for t in trades if t["result"] == "TP1" and t["actual_pnl_pct"])
    loss  = sum(abs(t["actual_pnl_pct"]) for t in trades if t["result"] == "SL" and t["actual_pnl_pct"])
    if loss == 0: return float("inf")
    return wins / loss

def _expectancy(trades):
    """Avg trade PnL (% per trade)."""
    pnls = [t["actual_pnl_pct"] for t in trades if t["actual_pnl_pct"] is not None]
    return sum(pnls) / len(pnls) if pnls else 0.0

def bucket_analysis(trades, key, buckets, label):
    """
    Analyse a feature by buckets.
    buckets: list of (label, predicate_fn)
    """
    rows = []
    for blabel, pred in buckets:
        subset = [t for t in trades if t.get(key) is not None and pred(t[key])]
        if not subset:
            continue
        n    = len(subset)
        wr   = _win_rate(subset)
        pf   = _pf(subset)
        epnl = _expectancy(subset)
        mfe  = _avg([t.get("mfe_pct") for t in subset])
        mae  = _avg([t.get("mae_pct") for t in subset])
        rows.append((blabel, n, wr, pf, epnl, mfe, mae))
    return rows

def print_bucket_table(rows, feature_name, min_n=1):
    print(f"\n  ── {feature_name} ──")
    print(f"  {'Bucket':<22} {'N':>3} {'WinRate':>8} {'PF':>6} {'AvgPnL':>8} {'AvgMFE':>8} {'AvgMAE':>8}")
    print("  " + "-"*68)
    for blabel, n, wr, pf, epnl, mfe, mae in rows:
        if n < min_n: continue
        pf_s  = f"{pf:6.2f}" if pf != float("inf") else "  INF"
        mfe_s = f"{mfe:6.2f}%" if mfe is not None else "   N/A"
        mae_s = f"{mae:6.2f}%" if mae is not None else "   N/A"
        mark = " ◀ 注意" if n >= 3 and wr >= 0.7 else ""
        mark = mark or (" ◀ 危险" if n >= 3 and wr <= 0.3 else "")
        print(f"  {blabel:<22} {n:>3} {wr*100:>7.1f}% {pf_s} {epnl:>7.2f}% {mfe_s} {mae_s}{mark}")


# ─────────────────────────────────────────────────────────────────────────────
# Full report
# ─────────────────────────────────────────────────────────────────────────────

def report(strategy_id: str, trades: list[dict]):
    sep = "=" * 72
    print(f"\n{sep}")
    print(f"  {strategy_id.upper()}  |  样本: {len(trades)} 笔  (⚠ 样本量极小，仅供方向参考)")
    print(sep)

    # ── A. 总体统计 ───────────────────────────────────────────────────────
    tp_all = [t for t in trades if t["result"] == "TP1"]
    sl_all = [t for t in trades if t["result"] == "SL"]
    wr     = _win_rate(trades)
    pf     = _pf(trades)
    exp    = _expectancy(trades)
    avg_tp = _avg([t["actual_pnl_pct"] for t in tp_all])
    avg_sl = _avg([t["actual_pnl_pct"] for t in sl_all])

    print(f"\n  A. 总体统计")
    print(f"     总笔数={len(trades)}  TP={len(tp_all)}  SL={len(sl_all)}")
    print(f"     胜率={wr*100:.1f}%  PF={pf:.2f}  Expectancy={exp:.2f}%/trade")
    print(f"     平均盈利={avg_tp:.2f}%  平均亏损={avg_sl:.2f}%")

    # ── B. LONG/SHORT 分开 ────────────────────────────────────────────────
    longs  = [t for t in trades if t["direction"] == "LONG"]
    shorts = [t for t in trades if t["direction"] == "SHORT"]
    print(f"\n  B. 方向拆解")
    for direction, grp in [("LONG", longs), ("SHORT", shorts)]:
        if not grp: continue
        tp_ = [t for t in grp if t["result"] == "TP1"]
        sl_ = [t for t in grp if t["result"] == "SL"]
        print(f"     {direction}: N={len(grp)} TP={len(tp_)} SL={len(sl_)} "
              f"胜率={_win_rate(grp)*100:.1f}% PF={_pf(grp):.2f}")

    # ── C. TP vs SL 特征均值 ──────────────────────────────────────────────
    print(f"\n  C. TP组 vs SL组 各特征均值/中位数")
    feature_pairs = [
        ("ret_24h",         "ret_24h (%)"),
        ("ret_3d",          "ret_3d (%)"),
        ("ret_7d",          "ret_7d (%)"),
        ("range_pos_30d",   "range_pos_30d"),
        ("atr_dist_4h",     "dist_4h_ema_atr"),
        ("slope_4h_ema20",  "4H EMA20 slope%"),
        ("slope_1h_ema20",  "1H EMA20 slope%"),
        ("vol_ratio_1h",    "vol_ratio_1h"),
        ("vol_ratio_15m",   "vol_ratio_15m"),
        ("rsi14",           "RSI14 (15m)"),
        ("pullback_depth",  "pullback_depth%"),
        ("fib_raw",         "Fibonacci pos"),
        ("dist_to_swing_pct","dist_swing%"),
        ("sig_candle_chg_pct","sig_candle%"),
        ("mfe_pct",         "MFE%"),
        ("mae_pct",         "MAE%"),
        ("hold_hours",      "hold_hours"),
        ("score",           "score"),
    ]
    tp_trades = [t for t in trades if t["result"] == "TP1"]
    sl_trades = [t for t in trades if t["result"] == "SL"]
    print(f"  {'特征':<24} {'TP均值':>9} {'TP中位':>9} {'SL均值':>9} {'SL中位':>9} {'差值':>8}")
    print("  " + "-"*65)
    for key, label in feature_pairs:
        tp_v = [t.get(key) for t in tp_trades if t.get(key) is not None]
        sl_v = [t.get(key) for t in sl_trades if t.get(key) is not None]
        if not tp_v and not sl_v:
            continue
        tp_avg = sum(tp_v) / len(tp_v) if tp_v else None
        tp_med = statistics.median(tp_v) if tp_v else None
        sl_avg = sum(sl_v) / len(sl_v) if sl_v else None
        sl_med = statistics.median(sl_v) if sl_v else None
        diff = tp_avg - sl_avg if tp_avg is not None and sl_avg is not None else None
        flag = ""
        if diff is not None:
            if abs(diff) > 0.5 * max(abs(tp_avg or 0), abs(sl_avg or 0), 0.001):
                flag = " ★"
        tp_a_s = f"{tp_avg:9.3f}" if tp_avg is not None else "      N/A"
        tp_m_s = f"{tp_med:9.3f}" if tp_med is not None else "      N/A"
        sl_a_s = f"{sl_avg:9.3f}" if sl_avg is not None else "      N/A"
        sl_m_s = f"{sl_med:9.3f}" if sl_med is not None else "      N/A"
        diff_s = f"{diff:8.3f}" if diff is not None else "     N/A"
        print(f"  {label:<24} {tp_a_s} {tp_m_s} {sl_a_s} {sl_m_s} {diff_s}{flag}")

    # ── D. 各特征分桶分析 ────────────────────────────────────────────────
    print(f"\n  D. 分桶归因分析  (N<3的桶不标注方向)")

    # 1. range_pos_30d
    rows = bucket_analysis(trades, "range_pos_30d", [
        ("<0.20",       lambda x: x < 0.20),
        ("0.20-0.35",   lambda x: 0.20 <= x < 0.35),
        ("0.35-0.50",   lambda x: 0.35 <= x < 0.50),
        ("0.50-0.65",   lambda x: 0.50 <= x < 0.65),
        ("0.65-0.80",   lambda x: 0.65 <= x < 0.80),
        (">0.80",       lambda x: x >= 0.80),
    ], "range_pos_30d")
    print_bucket_table(rows, "range_pos_30d (30日位置)", min_n=1)

    # 2. ret_7d
    rows = bucket_analysis(trades, "ret_7d", [
        ("<-10%",      lambda x: x < -10),
        ("-10~0%",     lambda x: -10 <= x < 0),
        ("0~5%",       lambda x: 0 <= x < 5),
        ("5~15%",      lambda x: 5 <= x < 15),
        ("15~30%",     lambda x: 15 <= x < 30),
        (">30%",       lambda x: x >= 30),
    ], "ret_7d")
    print_bucket_table(rows, "ret_7d (7日涨跌%)", min_n=1)

    # 3. vol_ratio_15m
    rows = bucket_analysis(trades, "vol_ratio_15m", [
        ("1.0-1.5x",   lambda x: 1.0 <= x < 1.5),
        ("1.5-2.0x",   lambda x: 1.5 <= x < 2.0),
        ("2.0-3.0x",   lambda x: 2.0 <= x < 3.0),
        ("3.0-5.0x",   lambda x: 3.0 <= x < 5.0),
        (">5.0x",      lambda x: x >= 5.0),
    ], "vol_ratio_15m")
    print_bucket_table(rows, "vol_ratio_15m (15m量比)", min_n=1)

    # 4. atr_dist_4h
    rows = bucket_analysis(trades, "atr_dist_4h", [
        ("<0.5 ATR",   lambda x: x < 0.5),
        ("0.5-1.0",    lambda x: 0.5 <= x < 1.0),
        ("1.0-1.5",    lambda x: 1.0 <= x < 1.5),
        ("1.5-2.5",    lambda x: 1.5 <= x < 2.5),
        (">2.5",       lambda x: x >= 2.5),
    ], "atr_dist_4h")
    print_bucket_table(rows, "dist_4h_ema_atr (4H距EMA距离)", min_n=1)

    # 5. RSI14
    rows = bucket_analysis(trades, "rsi14", [
        ("<30 超卖",    lambda x: x < 30),
        ("30-45",      lambda x: 30 <= x < 45),
        ("45-55",      lambda x: 45 <= x < 55),
        ("55-65",      lambda x: 55 <= x < 65),
        ("65-70",      lambda x: 65 <= x < 70),
        (">70 超买",   lambda x: x >= 70),
    ], "rsi14")
    print_bucket_table(rows, "RSI14 (15m)", min_n=1)

    # 6. Fibonacci
    fib_order = ["<0.382","0.382-0.500","0.500-0.618","0.618-0.786",">0.786"]
    fib_rows = []
    for fb in fib_order:
        subset = [t for t in trades if t.get("fib_bucket") == fb]
        if not subset: continue
        n    = len(subset)
        wr   = _win_rate(subset)
        pf   = _pf(subset)
        epnl = _expectancy(subset)
        mfe  = _avg([t.get("mfe_pct") for t in subset])
        mae  = _avg([t.get("mae_pct") for t in subset])
        fib_rows.append((fb, n, wr, pf, epnl, mfe, mae))
    print_bucket_table(fib_rows, "Fibonacci回撤位置 (1H swing)", min_n=1)

    # 7. slope_4h_ema20
    rows = bucket_analysis(trades, "slope_4h_ema20", [
        ("<-0.5%",     lambda x: x < -0.5),
        ("-0.5~0%",    lambda x: -0.5 <= x < 0),
        ("0~0.2%",     lambda x: 0 <= x < 0.2),
        ("0.2~0.5%",   lambda x: 0.2 <= x < 0.5),
        (">0.5%",      lambda x: x >= 0.5),
    ], "slope_4h_ema20")
    print_bucket_table(rows, "4H EMA20 slope (5bar%)", min_n=1)

    # 8. slope_1h_ema20
    rows = bucket_analysis(trades, "slope_1h_ema20", [
        ("<0%",        lambda x: x < 0),
        ("0~0.1%",     lambda x: 0 <= x < 0.1),
        ("0.1~0.3%",   lambda x: 0.1 <= x < 0.3),
        (">0.3%",      lambda x: x >= 0.3),
    ], "slope_1h_ema20")
    print_bucket_table(rows, "1H EMA20 slope (5bar%)", min_n=1)

    # 9. vol_ratio_1h
    rows = bucket_analysis(trades, "vol_ratio_1h", [
        ("<0.5x",      lambda x: x < 0.5),
        ("0.5-1.0x",   lambda x: 0.5 <= x < 1.0),
        ("1.0-2.0x",   lambda x: 1.0 <= x < 2.0),
        (">2.0x",      lambda x: x >= 2.0),
    ], "vol_ratio_1h")
    print_bucket_table(rows, "vol_ratio_1h (1H量比)", min_n=1)

    # 10. hold_hours
    rows = bucket_analysis(trades, "hold_hours", [
        ("<1h",        lambda x: x < 1),
        ("1-4h",       lambda x: 1 <= x < 4),
        ("4-12h",      lambda x: 4 <= x < 12),
        ("12-24h",     lambda x: 12 <= x < 24),
        (">24h",       lambda x: x >= 24),
    ], "hold_hours")
    print_bucket_table(rows, "持仓时间", min_n=1)

    # ── E. 各交易明细 ─────────────────────────────────────────────────────
    print(f"\n  E. 交易明细")
    print(f"  {'Symbol':<18} {'结果':>4} {'PnL%':>7} {'hold':>6} {'30d位':>7} {'7d%':>6} "
          f"{'dist4H':>6} {'vr15m':>6} {'RSI':>6} {'Fib':>14} {'MFE%':>6} {'MAE%':>6} {'score':>6}")
    print("  " + "-"*110)
    for t in trades:
        rp   = f"{t.get('range_pos_30d',0):.2f}" if t.get('range_pos_30d') is not None else " N/A"
        r7   = f"{t.get('ret_7d',0):5.1f}"        if t.get('ret_7d')       is not None else "  N/A"
        d4h  = f"{t.get('atr_dist_4h',0):.2f}"   if t.get('atr_dist_4h')  is not None else " N/A"
        vr15 = f"{t.get('vol_ratio_15m',0):.2f}" if t.get('vol_ratio_15m') is not None else " N/A"
        rsi  = f"{t.get('rsi14',0):.1f}"          if t.get('rsi14')         is not None else "  N/A"
        fib  = f"{t.get('fib_bucket','N/A')}"
        mfe  = f"{t.get('mfe_pct',0):.2f}"        if t.get('mfe_pct')      is not None else " N/A"
        mae  = f"{t.get('mae_pct',0):.2f}"        if t.get('mae_pct')      is not None else " N/A"
        sc   = f"{t.get('score')}"                 if t.get('score')         is not None else " N/A"
        icon = "✅" if t["result"] == "TP1" else "❌"
        print(f"  {t['symbol']:<18} {icon}{t['result']:>3} {t['actual_pnl_pct']:>6.2f}% "
              f"{t.get('hold_hours',0):>5.1f}h {rp:>7} {r7:>6} {d4h:>6} "
              f"{vr15:>6} {rsi:>6} {fib:>14} {mfe:>6} {mae:>6} {sc:>6}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("K1/K2 TP/SL Attribution Analysis")
    print("=" * 72)
    print(f"  重建 {len(TRADES_RAW)} 笔交易的信号时刻特征 (Binance Futures historical klines)")
    print()

    all_features = []
    for i, trade in enumerate(TRADES_RAW):
        print(f"[{i+1}/{len(TRADES_RAW)}] {trade['strategy_id']} {trade['symbol']}")
        feat = compute_trade_features(trade)
        all_features.append(feat)

    k1 = [t for t in all_features if t["strategy_id"] == "classic_k1"]
    k2 = [t for t in all_features if t["strategy_id"] == "classic_k2"]

    # ── Reports ────────────────────────────────────────────────────────────
    report("classic_k1", k1)
    report("classic_k2", k2)
    report("K1 + K2 合并", all_features)

    # ── Synthesis ─────────────────────────────────────────────────────────
    sep = "=" * 72
    print(f"\n{sep}")
    print("  综合归因结论  (⚠ 样本N=8，所有结论需K≥30验证)")
    print(sep)

    # Find strongest separators
    all_tp = [t for t in all_features if t["result"] == "TP1"]
    all_sl = [t for t in all_features if t["result"] == "SL"]

    print("\n  ── TP组共同特征 ──")
    for key, label in [
        ("range_pos_30d",  "30d位置"),
        ("vol_ratio_15m",  "15m量比"),
        ("rsi14",          "RSI14"),
        ("mfe_pct",        "MFE%"),
        ("mae_pct",        "MAE%"),
        ("atr_dist_4h",    "4H EMA距离(ATR)"),
        ("hold_hours",     "持仓时长h"),
    ]:
        tp_v = [t[key] for t in all_tp if t.get(key) is not None]
        sl_v = [t[key] for t in all_sl if t.get(key) is not None]
        if not tp_v or not sl_v: continue
        ta, sa = sum(tp_v)/len(tp_v), sum(sl_v)/len(sl_v)
        arrow = "↑" if ta > sa else "↓"
        print(f"  {label:<22}  TP均值={ta:6.3f}  SL均值={sa:6.3f}  差={ta-sa:+.3f} {arrow}")

    print("\n  ── SL组共同特征 ──")
    for key, label in [
        ("mae_pct",        "MAE (最大逆向%)"),
        ("hold_hours",     "持仓时长h"),
        ("vol_ratio_15m",  "15m量比"),
        ("slope_1h_ema20", "1H EMA斜率%"),
    ]:
        sl_v = [t[key] for t in all_sl if t.get(key) is not None]
        if not sl_v: continue
        print(f"  {label:<22}  SL均值={sum(sl_v)/len(sl_v):6.3f}")

    print("\n  ── 潜在过滤条件测试 ──")
    filters = [
        ("range_pos_30d < 0.65",    lambda t: t.get("range_pos_30d", 1) < 0.65),
        ("vol_ratio_15m >= 1.8x",   lambda t: (t.get("vol_ratio_15m") or 0) >= 1.8),
        ("vol_ratio_15m >= 2.0x",   lambda t: (t.get("vol_ratio_15m") or 0) >= 2.0),
        ("RSI14 < 65",              lambda t: (t.get("rsi14") or 99) < 65),
        ("RSI14 < 60",              lambda t: (t.get("rsi14") or 99) < 60),
        ("4H dist < 2.0 ATR",       lambda t: (t.get("atr_dist_4h") or 99) < 2.0),
        ("ret_7d < 20%",            lambda t: (t.get("ret_7d") or 99) < 20),
    ]
    print(f"  {'过滤条件':<30} {'剩余总N':>8} {'剩余TP':>8} {'剩余SL':>8} {'胜率':>8} {'PF':>8}")
    print("  " + "-"*70)
    for flabel, fpred in filters:
        sub = [t for t in all_features if fpred(t)]
        tp_ = [t for t in sub if t["result"] == "TP1"]
        sl_ = [t for t in sub if t["result"] == "SL"]
        wr_ = _win_rate(sub) if sub else 0
        pf_ = _pf(sub) if sub else 0
        pf_s = f"{pf_:6.2f}" if pf_ != float("inf") else "   INF"
        print(f"  {flabel:<30} {len(sub):>8} {len(tp_):>8} {len(sl_):>8} {wr_*100:>7.1f}% {pf_s}")

    print("\n  ⚠ 统计警告:")
    print("  ├─ K1=6笔, K2=2笔。以上所有数据均不具备统计显著性。")
    print("  ├─ 建议积累到K1≥30笔, K2≥20笔后重跑本分析。")
    print("  ├─ 部分早期交易(08-08)缺少scan_record, 特征由历史K线重建，")
    print("  │   可能因小币流动性不足或历史数据不完整而有误差。")
    print("  └─ 本报告为研究输出，禁止直接修改生产策略。")
    print()


def shadow_comparison_report() -> None:
    """
    ============================
    Shadow V2 对照实验报告
    ============================
    从生产DB中读取 classic_shadow_records 和对应的 v3_paper_orders，
    输出 K1原版 vs K1_SHADOW_V2 / K2原版 vs K2_SHADOW_V2 对比。

    样本门槛（定义在文件顶部）:
      K1 原版 settled >= 30  且  K1_SHADOW_V2 settled >= 15  → PROMOTE_CANDIDATE
      K2 原版 settled >= 20  且  K2_SHADOW_V2 settled >= 10  → PROMOTE_CANDIDATE
      否则 → CONTINUE_OBSERVATION

    晋级标准 (5项全部满足):
      1. Win Rate 高于原版
      2. PF 高于原版
      3. Expectancy 高于原版
      4. Avg MAE 不恶化 (shadow MAE <= 原版 MAE * 1.1)
      5. 信号数量不减少超过60% (pass_rate >= 40%)
    """
    # ── Sample thresholds ──────────────────────────────────────────────────────
    K1_SRC_MIN  = 30
    K2_SRC_MIN  = 20
    K1_SHD_MIN  = 15
    K2_SHD_MIN  = 10

    sep = "=" * 60
    print(f"\n{sep}")
    print("  Shadow V2 对照实验报告")
    print(f"  ⚠ 从DB读取实时数据 — 需要DATABASE_URL环境变量")
    print(sep)

    try:
        import os
        import psycopg2
        url = os.environ.get("DATABASE_URL")
        if not url:
            print("  ✗ DATABASE_URL 未设置，跳过 Shadow 报告")
            print("    (本地运行请设置 DATABASE_URL 或改为硬编码数据)")
            return
        conn = psycopg2.connect(url, connect_timeout=10)
    except Exception as exc:
        print(f"  ✗ DB连接失败: {exc}")
        return

    def _query(sql: str, params=()) -> list[dict]:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _scalar(sql: str, params=(), default=0):
        rows = _query(sql, params)
        return list(rows[0].values())[0] if rows else default

    def _wr(tp: int, n: int) -> float:
        return tp / n if n > 0 else 0.0

    def _pf(tp: int, sl: int, avg_win: float, avg_loss: float) -> float:
        if sl == 0:
            return float("inf") if tp > 0 else 0.0
        if avg_loss == 0:
            return float("inf")
        return abs(avg_win / avg_loss) * (tp / sl)

    def _expectancy(avg_win: float, avg_loss: float, wr: float) -> float:
        return wr * avg_win + (1 - wr) * avg_loss

    def _src_stats(strategy_id: str) -> dict:
        sql = """
            SELECT
                COUNT(*)                                                      AS n,
                SUM(CASE WHEN result IN ('TP1','TP2') THEN 1 ELSE 0 END)     AS tp,
                SUM(CASE WHEN result='SL'             THEN 1 ELSE 0 END)     AS sl,
                AVG(CAST(pnl_pct AS FLOAT))                                  AS avg_pnl,
                AVG(CASE WHEN result IN ('TP1','TP2') THEN CAST(pnl_pct AS FLOAT) END) AS avg_win,
                AVG(CASE WHEN result='SL'             THEN CAST(pnl_pct AS FLOAT) END) AS avg_loss
            FROM v3_paper_orders
            WHERE strategy_id=%s
              AND status IN ('TP1','TP2','SL','TIMEOUT','EXPIRED_NOT_FILLED')
        """
        rows = _query(sql, (strategy_id,))
        return rows[0] if rows else {}

    def _shadow_comparison(source_strategy: str, shadow_strategy: str) -> dict:
        sql = """
            SELECT
                COUNT(*)                                                           AS total_candidates,
                SUM(CASE WHEN sr.decision='PASS'   THEN 1 ELSE 0 END)             AS passed,
                SUM(CASE WHEN sr.decision='REJECT' THEN 1 ELSE 0 END)             AS rejected,
                SUM(CASE WHEN sr.decision='REJECT'
                      AND (po_src.result='TP1' OR po_src.result='TP2')
                                                   THEN 1 ELSE 0 END)             AS filtered_tp,
                SUM(CASE WHEN sr.decision='REJECT'
                      AND po_src.result='SL'       THEN 1 ELSE 0 END)             AS filtered_sl,
                SUM(CASE WHEN sr.decision='PASS'
                      AND (po_src.result='TP1' OR po_src.result='TP2')
                                                   THEN 1 ELSE 0 END)             AS kept_tp,
                SUM(CASE WHEN sr.decision='PASS'
                      AND po_src.result='SL'       THEN 1 ELSE 0 END)             AS kept_sl,
                -- reject reasons breakdown
                STRING_AGG(CASE WHEN sr.decision='REJECT' THEN sr.reject_reason END, ',')
                                                                                   AS all_reject_reasons
            FROM classic_shadow_records sr
            LEFT JOIN v3_paper_orders po_src
                ON po_src.signal_id = sr.source_signal_id
            WHERE sr.source_strategy=%s AND sr.shadow_strategy=%s
        """
        rows = _query(sql, (source_strategy, shadow_strategy))
        return rows[0] if rows else {}

    def _report_pair(src_id: str, shd_id: str, label: str,
                     src_min: int, shd_min: int) -> None:
        print(f"\n{'─'*55}")
        print(f"  {label}")
        print(f"{'─'*55}")

        src_st = _src_stats(src_id)
        shd_st = _src_stats(shd_id)
        cmp    = _shadow_comparison(src_id, shd_id)

        src_n  = int(src_st.get("n") or 0)
        src_tp = int(src_st.get("tp") or 0)
        src_sl = int(src_st.get("sl") or 0)
        src_aw = float(src_st.get("avg_win")  or 0)
        src_al = float(src_st.get("avg_loss") or 0)
        src_ap = float(src_st.get("avg_pnl")  or 0)
        src_wr = _wr(src_tp, src_n)
        src_pf = _pf(src_tp, src_sl, src_aw, src_al)
        src_ex = _expectancy(src_aw, src_al, src_wr)

        shd_n  = int(shd_st.get("n") or 0)
        shd_tp = int(shd_st.get("tp") or 0)
        shd_sl = int(shd_st.get("sl") or 0)
        shd_aw = float(shd_st.get("avg_win")  or 0)
        shd_al = float(shd_st.get("avg_loss") or 0)
        shd_ap = float(shd_st.get("avg_pnl")  or 0)
        shd_wr = _wr(shd_tp, shd_n)
        shd_pf = _pf(shd_tp, shd_sl, shd_aw, shd_al)
        shd_ex = _expectancy(shd_aw, shd_al, shd_wr)

        total_c     = int(cmp.get("total_candidates") or 0)
        passed      = int(cmp.get("passed")     or 0)
        rejected    = int(cmp.get("rejected")   or 0)
        filtered_tp = int(cmp.get("filtered_tp") or 0)
        filtered_sl = int(cmp.get("filtered_sl") or 0)
        kept_tp     = int(cmp.get("kept_tp")    or 0)
        kept_sl     = int(cmp.get("kept_sl")    or 0)
        pass_rate   = passed / total_c if total_c > 0 else 0.0

        pf_s = lambda x: f"{x:.2f}" if x != float("inf") else "INF"

        print(f"\n  原版 ({src_id}):")
        print(f"    signals={src_n}  TP={src_tp}  SL={src_sl}")
        print(f"    WR={src_wr*100:.1f}%  PF={pf_s(src_pf)}  Expectancy={src_ex:+.2f}%  AvgPnL={src_ap:+.2f}%")
        if src_n < src_min:
            print(f"    ⚠ INSUFFICIENT_SAMPLE ({src_n}/{src_min})")

        print(f"\n  Shadow ({shd_id}):")
        print(f"    候选={total_c}  通过={passed}({pass_rate*100:.0f}%)  拒绝={rejected}")
        print(f"    settled={shd_n}  TP={shd_tp}  SL={shd_sl}")
        print(f"    WR={shd_wr*100:.1f}%  PF={pf_s(shd_pf)}  Expectancy={shd_ex:+.2f}%  AvgPnL={shd_ap:+.2f}%")
        if shd_n < shd_min:
            print(f"    ⚠ INSUFFICIENT_SAMPLE ({shd_n}/{shd_min})")

        print(f"\n  过滤效果:")
        print(f"    过滤掉 TP={filtered_tp}  过滤掉 SL={filtered_sl}  "
              f"（保留 TP={kept_tp}  保留 SL={kept_sl}）")

        # Reject reason breakdown
        raw_rrs = cmp.get("all_reject_reasons") or ""
        if raw_rrs:
            from collections import Counter
            rr_counts = Counter(r for r in raw_rrs.split(",") if r.strip())
            top_rrs = rr_counts.most_common(5)
            print(f"    拒绝原因: {dict(top_rrs)}")

        # Promotion evaluation
        print(f"\n  晋级评估:")
        if src_n < src_min or shd_n < shd_min:
            print(f"    → CONTINUE_OBSERVATION  (样本不足，禁止自动建议替换生产策略)")
            return

        criteria = {
            "WR提升":          shd_wr > src_wr,
            "PF提升":          (shd_pf > src_pf) if src_pf != float("inf") else False,
            "Expectancy提升":  shd_ex > src_ex,
            "MAE不恶化":       True,  # requires MAE data from paper orders (not tracked here yet)
            "信号≥40%保留":    pass_rate >= 0.40,
        }
        all_pass = all(criteria.values())
        for name, ok in criteria.items():
            mark = "✅" if ok else "❌"
            print(f"    {mark} {name}")

        if all_pass:
            print(f"\n    → PROMOTE_CANDIDATE  ⭐")
            print(f"       Shadow V2 满足晋级标准，可考虑替换生产策略（需人工确认）")
        else:
            print(f"\n    → CONTINUE_OBSERVATION  (尚未满足全部晋级标准)")

    # ── Run reports ────────────────────────────────────────────────────────────
    _report_pair(
        "classic_k1", "classic_k1_shadow_v2",
        "K1原版  vs  K1_SHADOW_V2",
        K1_SRC_MIN, K1_SHD_MIN,
    )
    _report_pair(
        "classic_k2", "classic_k2_shadow_v2",
        "K2原版  vs  K2_SHADOW_V2",
        K2_SRC_MIN, K2_SHD_MIN,
    )

    conn.close()
    print(f"\n{sep}")
    print("  禁止在K1原版<30笔 / K2原版<20笔之前根据以上数据修改生产策略。")
    print(sep)


if __name__ == "__main__":
    import sys
    if "--shadow" in sys.argv:
        # python analysis/k1_k2_attribution.py --shadow
        shadow_comparison_report()
    else:
        main()
        if "--with-shadow" in sys.argv:
            shadow_comparison_report()
