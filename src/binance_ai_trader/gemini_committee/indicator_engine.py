from __future__ import annotations

from typing import Any


def _ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _atr_pct(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        prev_close = closes[i - 1]
        tr = max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close))
        trs.append(tr)
    atr = sum(trs[-period:]) / period
    if closes[-1] == 0:
        return None
    return round(atr / closes[-1] * 100, 4)


def _volume_ratio(volumes: list[float], period: int = 20) -> float | None:
    if len(volumes) < period + 1:
        return None
    avg = sum(volumes[-period - 1 : -1]) / period
    if avg == 0:
        return None
    return round(volumes[-1] / avg, 4)


def _trend(closes: list[float], ema_short: float | None, ema_long: float | None) -> str:
    if ema_short is None or ema_long is None or len(closes) < 3:
        return "UNKNOWN"
    slope = closes[-1] - closes[-3]
    if ema_short > ema_long and slope > 0:
        return "UP"
    if ema_short < ema_long and slope < 0:
        return "DOWN"
    return "SIDEWAYS"


def compute_indicators(klines: list[dict[str, Any]], timeframe: str) -> dict[str, str]:
    if not klines:
        return {}

    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    volumes = [float(k["volume"]) for k in klines]

    e10 = _ema(closes, 10)
    e20 = _ema(closes, 20)
    e60 = _ema(closes, 60)
    rsi = _rsi(closes, 14)
    atr = _atr_pct(highs, lows, closes, 14)
    vol_ratio = _volume_ratio(volumes, 20)
    swing_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    swing_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)

    trend = _trend(closes, e10, e20)

    def fmt(v: float | None, decimals: int = 6) -> str:
        return "UNKNOWN" if v is None else str(round(v, decimals))

    result: dict[str, str] = {
        "trend": trend,
        "ema10": fmt(e10),
        "ema20": fmt(e20),
        "rsi14": fmt(rsi, 2),
        "atr_pct": fmt(atr, 4),
        "volume_ratio_20": fmt(vol_ratio, 4),
        "recent_swing_high": fmt(swing_high),
        "recent_swing_low": fmt(swing_low),
    }
    if timeframe in ("h1", "h4", "d1"):
        result["ema60"] = fmt(e60)
    if timeframe == "d1":
        result["change_30d"] = fmt(
            (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] != 0 else None, 2
        )
        result["recent_high_30d"] = fmt(max(highs))
        result["recent_low_30d"] = fmt(min(lows))
    return result
