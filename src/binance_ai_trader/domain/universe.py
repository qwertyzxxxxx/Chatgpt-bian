from __future__ import annotations

from collections.abc import Iterable

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.domain.models import Contract, Ticker24h, UniverseMember


def is_leveraged_token(base_asset: str, suffixes: tuple[str, ...]) -> bool:
    return any(base_asset.endswith(suffix) and len(base_asset) > len(suffix) for suffix in suffixes)


def build_universe(
    contracts: Iterable[Contract],
    tickers: Iterable[Ticker24h],
    config: UniverseConfig,
) -> tuple[UniverseMember, ...]:
    ticker_by_symbol = {ticker.symbol: ticker for ticker in tickers}
    members: list[UniverseMember] = []

    for contract in contracts:
        ticker = ticker_by_symbol.get(contract.symbol)
        if (
            contract.contract_type != "PERPETUAL"
            or contract.status != "TRADING"
            or contract.quote_asset != "USDT"
            or contract.margin_asset != "USDT"
            or contract.symbol in config.denied_symbols
            or contract.base_asset in config.stablecoin_base_assets
            or is_leveraged_token(contract.base_asset, config.leveraged_token_suffixes)
            or ticker is None
            or ticker.quote_volume <= config.minimum_quote_volume_24h
        ):
            continue
        members.append(UniverseMember(contract=contract, ticker=ticker))

    return tuple(sorted(members, key=lambda member: member.symbol))
