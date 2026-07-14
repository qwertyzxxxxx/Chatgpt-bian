"""Classic universe: 30M USDT filter → top 20 gainers + top 20 losers."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.domain.models import Contract, Ticker24h
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient

log = logging.getLogger(__name__)

_STABLECOINS = frozenset({
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD",
    "EUR", "GBP", "BRL", "PAXG", "UST", "USDS",
})

_LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "2L", "2S", "3L", "3S")


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    symbol: str
    base_asset: str
    change_24h: Decimal
    quote_volume_24h: Decimal
    last_price: Decimal


def _is_valid(contract: Contract, ticker: Ticker24h) -> bool:
    """True if the symbol passes the Classic universe filter."""
    if contract.contract_type != "PERPETUAL":
        return False
    if contract.status != "TRADING":
        return False
    if contract.quote_asset != "USDT":
        return False
    ba = contract.base_asset.upper()
    if ba in _STABLECOINS:
        return False
    if any(ba.endswith(suf) for suf in _LEVERAGED_SUFFIXES):
        return False
    if ticker.quote_volume < CFG.min_quote_volume_24h:
        return False
    if ticker.last_price <= 0:
        return False
    return True


def build_universe(
    client: BinancePublicClient,
) -> tuple[list[UniverseEntry], list[UniverseEntry]]:
    """Return (top_gainers[:20], top_losers[:20]) from universe >= 30M USDT."""
    contracts = {c.symbol: c for c in client.exchange_info()}
    tickers   = {t.symbol: t for t in client.tickers_24h()}

    eligible: list[UniverseEntry] = []
    for sym, ticker in tickers.items():
        contract = contracts.get(sym)
        if contract is None:
            continue
        if not _is_valid(contract, ticker):
            continue
        eligible.append(UniverseEntry(
            symbol=sym,
            base_asset=contract.base_asset,
            change_24h=ticker.price_change_percent,
            quote_volume_24h=ticker.quote_volume,
            last_price=ticker.last_price,
        ))

    gainers = sorted(eligible, key=lambda e: (-e.change_24h, -e.quote_volume_24h))
    gainers = [e for e in gainers if e.change_24h > 0][: CFG.universe_pool_size]

    losers  = sorted(eligible, key=lambda e: (e.change_24h, -e.quote_volume_24h))
    losers  = [e for e in losers  if e.change_24h < 0][: CFG.universe_pool_size]

    log.info(
        "[Classic/Universe] eligible=%d gainers=%d losers=%d (min_vol=%sM)",
        len(eligible), len(gainers), len(losers),
        int(CFG.min_quote_volume_24h / 1_000_000),
    )
    return gainers, losers
