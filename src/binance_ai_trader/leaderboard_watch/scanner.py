from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RankedSymbol:
    symbol: str
    rank_type: str
    rank_position: int
    change_24h: str
    quote_volume: str


def fetch_leaderboard(
    top_n: int = 10,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
) -> list[RankedSymbol]:
    """Fetch top gainers, losers, and volume leaders from Binance USDT perpetuals."""
    url = f"{base_url}/fapi/v1/ticker/24hr"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            tickers = json.loads(resp.read())
    except Exception as exc:
        logger.warning("Leaderboard fetch failed: %s", exc)
        return []

    usdt = [
        t for t in tickers
        if str(t.get("symbol", "")).endswith("USDT")
        and float(t.get("quoteVolume", 0)) > 0
    ]

    def pct(t: dict) -> float:
        try:
            return float(t.get("priceChangePercent", 0))
        except (ValueError, TypeError):
            return 0.0

    def vol(t: dict) -> float:
        try:
            return float(t.get("quoteVolume", 0))
        except (ValueError, TypeError):
            return 0.0

    gainers = sorted(usdt, key=pct, reverse=True)[:top_n]
    losers = sorted(usdt, key=pct)[:top_n]
    by_volume = sorted(usdt, key=vol, reverse=True)[:top_n]

    results: list[RankedSymbol] = []
    seen: set[str] = set()

    for rank, t in enumerate(gainers, 1):
        sym = t["symbol"]
        seen.add(sym)
        results.append(RankedSymbol(
            symbol=sym,
            rank_type="GAINER",
            rank_position=rank,
            change_24h=str(round(pct(t), 4)),
            quote_volume=str(round(vol(t), 0)),
        ))

    for rank, t in enumerate(losers, 1):
        sym = t["symbol"]
        if sym not in seen:
            seen.add(sym)
        results.append(RankedSymbol(
            symbol=sym,
            rank_type="LOSER",
            rank_position=rank,
            change_24h=str(round(pct(t), 4)),
            quote_volume=str(round(vol(t), 0)),
        ))

    for rank, t in enumerate(by_volume, 1):
        sym = t["symbol"]
        if sym not in seen:
            seen.add(sym)
        results.append(RankedSymbol(
            symbol=sym,
            rank_type="VOLUME",
            rank_position=rank,
            change_24h=str(round(pct(t), 4)),
            quote_volume=str(round(vol(t), 0)),
        ))

    return results
