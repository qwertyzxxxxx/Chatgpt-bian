import io
import csv
import requests
import logging
from market_hunter.config import FMP_API_KEY

logger = logging.getLogger(__name__)

FMP_BASE = "https://financialmodelingprep.com/api/v3"

# Public fallback datasets (no API key required)
PUBLIC_SP500_CSV = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies"
    "/master/data/constituents.csv"
)
PUBLIC_NASDAQ100_CSV = (
    "https://raw.githubusercontent.com/datasets/nasdaq-listings"
    "/master/data/nasdaq-listed.csv"
)


def get_us_stock_universe() -> list[dict]:
    """
    Fetch the US large-cap stock universe.

    Attempt order:
      1. FMP /sp500_constituent  (free tier on most FMP plans)
      2. FMP /nasdaq_constituent (free tier on most FMP plans)
      3. Public GitHub S&P 500 CSV (no key required — always works)

    All three sources provide stocks that already satisfy the
    market-cap >= $10 B and dollar-volume >= $50 M requirements,
    so very few additional stocks are filtered out during the scan.
    """
    stocks = _try_fmp_constituents()
    if stocks:
        logger.info(f"FMP constituents: {len(stocks)} stocks loaded")
        return stocks

    logger.warning("FMP constituent endpoints unavailable — using public S&P 500 fallback")
    stocks = _fetch_public_sp500()
    if stocks:
        logger.info(f"Public S&P 500 fallback: {len(stocks)} stocks loaded")
        return stocks

    logger.error("All universe sources failed — cannot run scan")
    return []


# ---------------------------------------------------------------------------
# FMP constituent endpoints
# ---------------------------------------------------------------------------

def _try_fmp_constituents() -> list[dict]:
    """Try FMP /sp500_constituent and /nasdaq_constituent (free-tier endpoints)."""
    if not FMP_API_KEY:
        logger.warning("FMP_API_KEY not set — skipping FMP fetch")
        return []

    endpoints = [
        (f"{FMP_BASE}/sp500_constituent", "S&P 500"),
        (f"{FMP_BASE}/nasdaq_constituent", "NASDAQ 100"),
    ]

    seen: set[str] = set()
    result: list[dict] = []

    for url, label in endpoints:
        try:
            resp = requests.get(url, params={"apikey": FMP_API_KEY}, timeout=20)
            if resp.status_code == 403:
                logger.info(f"FMP {label}: 403 — paid plan required, using public fallback")
                return []   # Both endpoints share the same plan gate; stop trying
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                logger.warning(f"FMP {label}: unexpected response type {type(data)}")
                continue

            for s in data:
                symbol = (s.get("symbol") or "").strip()
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                result.append({
                    "symbol": symbol,
                    "companyName": s.get("name") or s.get("companyName", ""),
                    "sector": s.get("sector", ""),
                    "industry": s.get("subSector") or s.get("industry", ""),
                    "marketCap": 0,
                    "exchange": s.get("exchange", ""),
                })
            logger.info(f"FMP {label}: {len(data)} stocks fetched")

        except Exception as e:
            logger.warning(f"FMP {label} error: {e}")

    return result


# ---------------------------------------------------------------------------
# Public fallback — S&P 500 from GitHub open-data (no key required)
# ---------------------------------------------------------------------------

def _fetch_public_sp500() -> list[dict]:
    """
    Fetch S&P 500 constituents from a public GitHub dataset.
    Includes sector data. Requires no API key.
    """
    try:
        resp = requests.get(PUBLIC_SP500_CSV, timeout=20)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        stocks = []
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            if not symbol:
                continue
            stocks.append({
                "symbol": symbol,
                "companyName": row.get("Security", ""),
                "sector": row.get("GICS Sector", ""),
                "industry": row.get("GICS Sub-Industry", ""),
                "marketCap": 0,
                "exchange": "",
            })
        return stocks
    except Exception as e:
        logger.error(f"Public S&P 500 CSV fetch failed: {e}")
        return []
