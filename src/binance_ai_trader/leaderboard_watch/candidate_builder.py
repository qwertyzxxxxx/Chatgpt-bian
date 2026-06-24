from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone

from binance_ai_trader.gemini_committee.indicator_engine import compute_indicators

from .models import WatchCandidateForGemini, WatchItem

logger = logging.getLogger(__name__)


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
) -> list[WatchCandidateForGemini]:
    candidates: list[WatchCandidateForGemini] = []

    for item in items:
        m15 = _fetch_klines(item.symbol, "15m", 96, base_url, timeout)
        h1 = _fetch_klines(item.symbol, "1h", 72, base_url, timeout)
        h4 = _fetch_klines(item.symbol, "4h", 60, base_url, timeout)
        d1 = _fetch_klines(item.symbol, "1d", 30, base_url, timeout)

        any_data = any([m15, h1, h4, d1])
        full_data = all([m15, h1, h4, d1])
        data_quality = "FULL" if full_data else ("PARTIAL" if any_data else "POOR")

        def ind(klines: list[dict], tf: str) -> dict:
            if not klines:
                return {}
            out = compute_indicators(klines, tf)
            # Expose spec field names alongside the indicator-engine outputs so
            # the JSON sent to Gemini always contains volume_ratio/recent_high/
            # recent_low (the engine names them *_20 / recent_swing_*). Additive
            # aliases only — the shared indicator_engine is left untouched.
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

    return candidates
