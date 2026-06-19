from __future__ import annotations

import logging
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from .models import (
    StrategyResult,
    RESULT_OPEN, RESULT_TP1, RESULT_TP2, RESULT_SL, RESULT_TIMEOUT,
)

log = logging.getLogger(__name__)

_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
_TIMEOUT_DAYS = 7
_KLINE_INTERVAL = "15m"
_KLINE_LIMIT = 500


def _safe_float(v: str) -> Optional[float]:
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _fetch_klines(symbol: str, start_ms: int) -> List[list]:
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": _KLINE_INTERVAL,
        "startTime": start_ms,
        "limit": _KLINE_LIMIT,
    })
    url = f"{_KLINE_URL}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        log.warning("klines fetch failed for %s: %s", symbol, exc)
        return []


def _parse_opened_at(opened_at: str) -> Optional[datetime]:
    try:
        normalized = opened_at.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(opened_at, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    log.warning("Cannot parse opened_at: %r", opened_at)
    return None


def settle_one(sr: StrategyResult, now: Optional[datetime] = None) -> StrategyResult:
    if sr.result != RESULT_OPEN:
        return sr

    if now is None:
        now = datetime.now(timezone.utc)

    opened_dt = _parse_opened_at(sr.opened_at)
    if opened_dt is None:
        log.warning("Cannot parse opened_at for %s: %s", sr.result_id, sr.opened_at)
        return sr

    age = now - opened_dt
    if age > timedelta(days=_TIMEOUT_DAYS):
        sr.result = RESULT_TIMEOUT
        sr.closed_at = now.strftime("%Y-%m-%dT%H:%M:%S")
        sr.duration_minutes = int(age.total_seconds() / 60)
        return sr

    entry = _safe_float(sr.entry)
    sl = _safe_float(sr.stop_loss)
    tp1 = _safe_float(sr.tp1)
    tp2 = _safe_float(sr.tp2)

    if entry is None or sl is None or tp1 is None:
        return sr

    direction = sr.direction.upper()
    if direction not in ("LONG", "SHORT"):
        return sr

    start_ms = int(opened_dt.timestamp() * 1000) + 1
    klines = _fetch_klines(sr.symbol, start_ms)

    for k in klines:
        k_open_ms = int(k[0])
        k_close_ms = int(k[6])
        k_high = float(k[2])
        k_low = float(k[3])
        k_close_time = datetime.fromtimestamp(k_close_ms / 1000, tz=timezone.utc)

        hit_result, hit_time = _check_candle(direction, k_high, k_low, sl, tp1, tp2, k_close_time)
        if hit_result:
            opened_epoch = opened_dt.timestamp()
            closed_epoch = hit_time.timestamp()
            duration = int((closed_epoch - opened_epoch) / 60)
            pnl, rr = _calc_pnl(direction, entry, sl, tp1, tp2, hit_result)
            sr.result = hit_result
            sr.closed_at = hit_time.strftime("%Y-%m-%dT%H:%M:%S")
            sr.duration_minutes = max(0, duration)
            sr.pnl_pct = pnl
            sr.rr_realized = rr
            return sr

    return sr


def _check_candle(
    direction: str,
    high: float,
    low: float,
    sl: float,
    tp1: float,
    tp2: Optional[float],
    candle_time: datetime,
) -> Tuple[Optional[str], Optional[datetime]]:
    if direction == "LONG":
        if low <= sl:
            return RESULT_SL, candle_time
        if tp2 is not None and high >= tp2:
            return RESULT_TP2, candle_time
        if high >= tp1:
            return RESULT_TP1, candle_time
    else:
        if high >= sl:
            return RESULT_SL, candle_time
        if tp2 is not None and low <= tp2:
            return RESULT_TP2, candle_time
        if low <= tp1:
            return RESULT_TP1, candle_time
    return None, None


def _calc_pnl(
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: Optional[float],
    hit: str,
) -> Tuple[Optional[float], Optional[float]]:
    risk = abs(entry - sl)
    if risk == 0:
        return None, None
    if hit == RESULT_TP1:
        reward = abs(tp1 - entry)
    elif hit == RESULT_TP2 and tp2 is not None:
        reward = abs(tp2 - entry)
    elif hit == RESULT_SL:
        reward = -risk
    else:
        return None, None

    if direction == "LONG":
        pnl = ((entry + reward) / entry - 1) * 100 if hit != RESULT_SL else -risk / entry * 100
    else:
        pnl = (1 - (entry - reward) / entry) * 100 if hit != RESULT_SL else -risk / entry * 100

    if hit == RESULT_SL:
        rr = -1.0
        pnl = -risk / entry * 100
    else:
        rr = round(reward / risk, 2)
        pnl = round(reward / entry * 100, 4)

    return round(pnl, 4), rr


def settle_all(open_results: List[StrategyResult], now: Optional[datetime] = None) -> List[StrategyResult]:
    settled = []
    for sr in open_results:
        updated = settle_one(sr, now=now)
        settled.append(updated)
    return settled
