from decimal import Decimal
import unittest

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.domain.models import Contract, Ticker24h
from binance_ai_trader.domain.universe import build_universe, is_leveraged_token


def contract(symbol: str, base: str, **overrides: object) -> Contract:
    values = {
        "symbol": symbol,
        "base_asset": base,
        "quote_asset": "USDT",
        "margin_asset": "USDT",
        "contract_type": "PERPETUAL",
        "status": "TRADING",
        "price_precision": 2,
        "quantity_precision": 3,
        "tick_size": Decimal("0.01"),
        "step_size": Decimal("0.001"),
    }
    values.update(overrides)
    return Contract(**values)  # type: ignore[arg-type]


def ticker(symbol: str, volume: str) -> Ticker24h:
    return Ticker24h(symbol, Decimal(volume), Decimal("1.5"), 1)


class UniverseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = UniverseConfig(
            minimum_quote_volume_24h=Decimal("5000000"),
            stablecoin_base_assets=frozenset({"USDC"}),
            leveraged_token_suffixes=("UP", "DOWN", "BULL", "BEAR"),
            denied_symbols=frozenset({"DENYUSDT"}),
        )

    def test_keeps_only_eligible_contracts_above_strict_volume_threshold(self) -> None:
        contracts = (
            contract("BTCUSDT", "BTC"),
            contract("LOWUSDT", "LOW"),
            contract("USDCUSDT", "USDC"),
            contract("ETHUPUSDT", "ETHUP"),
            contract("DENYUSDT", "DENY"),
            contract("DATEDUSDT", "DATED", contract_type="CURRENT_QUARTER"),
            contract("PAUSEDUSDT", "PAUSED", status="SETTLING"),
            contract("WRONGQUOTE", "WRONG", quote_asset="USDC"),
        )
        tickers = tuple(ticker(item.symbol, "5000001") for item in contracts)
        tickers = tuple(ticker(item.symbol, "5000000") if item.symbol == "LOWUSDT" else item for item in tickers)

        result = build_universe(contracts, tickers, self.config)

        self.assertEqual(["BTCUSDT"], [item.symbol for item in result])

    def test_leveraged_suffix_requires_a_prefix(self) -> None:
        self.assertTrue(is_leveraged_token("ETHBULL", ("BULL",)))
        self.assertFalse(is_leveraged_token("BULL", ("BULL",)))


if __name__ == "__main__":
    unittest.main()
