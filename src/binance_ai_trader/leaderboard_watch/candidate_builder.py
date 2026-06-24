from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone

from binance_ai_trader.gemini_committee.indicator_engine import compute_indicators

from .models import WatchCandidateForGemini, WatchItem

logger = logging.getLogger(__name__)

_GEMINI_LIMITS: dict[str, int] = {"15m": 96, "1h": 72, "4h": 60, "1d": 30}


def _fetch_klines(
    symbol: str,
    interval: str,
    limit: int,
    base_url: str,
    timeout: float = 10.0,
) -> list[dict]:
    import urllib.parse
    params = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": str(limit)})
    url = f"{base_url}/fapi/v1/klines?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = json.loads(resp.read())
        return [
            {
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in raw
        ]
    except Exception as exc:
        logger.debug("klines fetch failed %s %s: %s", symbol, interval, exc)
        return []


def _klines_to_dicts(klines) -> list[dict]:
    """Convert Kline domain objects to the dict format expected by compute_indicators."""
    return [
        {
            "open": float(k.open),
            "high": float(k.high),
            "low": float(k.low),
            "close": float(k.close),
            "volume": float(k.volume),
        }
        for k in klines
    ]


def _minutes_since(iso: str) -> int:
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - ts).total_seconds() / 60)
    except Exception:
        return 0


def build_candidates(
    items: list[WatchItem],
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    cache=None,
) -> list[WatchCandidateForGemini]:
    """Build Gemini candidates from the watch pool.

    Parameters
    ----------
    items:
        Active watch-pool items to evaluate.
    base_url:
        Binance FAPI base URL (fallback when *cache* is None or misses).
    timeout:
        HTTP timeout for direct API fetches.
    cache:
        Optional ``MarketDataCache`` instance.  When provided, kline data is
        read from the cache (SQLite first, gap-fill from API) instead of doing
        a full Binance pull for each symbol.  Pass ``None`` to use the
        original direct-fetch behaviour.
    """
    candidates: list[WatchCandidateForGemini] = []
    cache_hits = 0
    cache_misses = 0

    for item in items:
        if cache is not None:
            m15_klines = cache.get_klines(item.symbol, "15m", _GEMINI_LIMITS["15m"])
            h1_klines = cache.get_klines(item.symbol, "1h", _GEMINI_LIMITS["1h"])
            h4_klines = cache.get_klines(item.symbol, "4h", _GEMINI_LIMITS["4h"])
            d1_klines = cache.get_klines(item.symbol, "1d", _GEMINI_LIMITS["1d"])
            m15 = _klines_to_dicts(m15_klines)
            h1 = _klines_to_dicts(h1_klines)
            h4 = _klines_to_dicts(h4_klines)
            d1 = _klines_to_dicts(d1_klines)
            stats = cache.stats()
            cache_hits = stats.cache_hit
            cache_misses = stats.cache_miss
        else:
            m15 = _fetch_klines(item.symbol, "15m", _GEMINI_LIMITS["15m"], base_url, timeout)
            h1 = _fetch_klines(item.symbol, "1h", _GEMINI_LIMITS["1h"], base_url, timeout)
            h4 = _fetch_klines(item.symbol, "4h", _GEMINI_LIMITS["4h"], base_url, timeout)
            d1 = _fetch_klines(item.symbol, "1d", _GEMINI_LIMITS["1d"], base_url, timeout)

        any_data = any([m15, h1, h4, d1])
        full_data = all([m15, h1, h4, d1])
        data_quality = "FULL" if full_data else ("PARTIAL" if any_data else "POOR")

        def ind(klines: list[dict], tf: str) -> dict:
            if not klines:
                return {}
            out = compute_indicators(klines, tf)
            if "volume_ratio" not in out and "volume_ratio_20" in out:
                out["volume_ratio"] = out["volume_ratio_20"]
            if "recent_high" not in out and "recent_swing_high" in out:
                out["recent_high"] = out["recent_swing_high"]
            if "recent_low" not in out and "recent_swing_low" in out:
                out["recent_low"] = out["recent_swing_low"]
            return out

        candidates.append(WatchCandidateForGemini(
            symbol=item.symbol,
            latest_rank_type=item.latest_rank_type,
            latest_rank_position=item.latest_rank_position,
            best_rank_position=item.best_rank_position,
            latest_change_24h=item.latest_change_24h,
            first_change_24h=item.first_change_24h,
            quote_volume=item.quote_volume,
            active_duration_minutes=_minutes_since(item.first_seen_at),
            appearances_24h=item.appearances_24h,
            gainer_candidate=item.latest_rank_type == "GAINER",
            loser_candidate=item.latest_rank_type == "LOSER",
            volume_candidate=item.latest_rank_type == "VOLUME",
            data_quality=data_quality,
            m15=ind(m15, "m15"),
            h1=ind(h1, "h1"),
            h4=ind(h4, "h4"),
            d1=ind(d1, "d1"),
        ))

    logger.debug(
        "build_candidates: count=%d cache_hit=%d cache_miss=%d",
        len(candidates), cache_hits, cache_misses,
    )
    return candidates
