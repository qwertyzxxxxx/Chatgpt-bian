import json
from pathlib import Path
import unittest

from binance_ai_trader.infrastructure.binance_public import BinancePublicApiError, BinancePublicClient

FIXTURES = Path(__file__).parents[1] / "fixtures"


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class StubClient(BinancePublicClient):
    def __init__(self, payloads: dict[str, object]) -> None:
        super().__init__(max_retries=0)
        self.payloads = payloads

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        return self.payloads[path]


class BinancePublicClientTest(unittest.TestCase):
    def test_maps_exchange_info_and_ticker_contracts(self) -> None:
        client = StubClient(
            {
                "/fapi/v1/exchangeInfo": fixture("exchange_info.json"),
                "/fapi/v1/ticker/24hr": fixture("tickers_24h.json"),
            }
        )

        contracts = client.exchange_info()
        tickers = client.tickers_24h()

        self.assertEqual("BTCUSDT", contracts[0].symbol)
        self.assertEqual("0.10", str(contracts[0].tick_size))
        self.assertEqual("10000000.50", str(tickers[0].quote_volume))
        self.assertEqual("2.75", str(tickers[0].price_change_percent))

    def test_removes_open_candle(self) -> None:
        client = StubClient({"/fapi/v1/klines": fixture("klines.json")})

        result = client.klines("BTCUSDT", "15m", now_ms=1710002000000)

        self.assertEqual(2, len(result))
        self.assertEqual(1710000900000, result[-1].open_time_ms)

    def test_rejects_kline_gap(self) -> None:
        rows = fixture("klines.json")
        assert isinstance(rows, list)
        rows.pop(1)
        client = StubClient({"/fapi/v1/klines": rows})

        with self.assertRaises(BinancePublicApiError):
            client.klines("BTCUSDT", "15m", now_ms=1710004000000)

    def test_rejects_unapproved_interval(self) -> None:
        client = StubClient({})
        with self.assertRaises(ValueError):
            client.klines("BTCUSDT", "5m")


if __name__ == "__main__":
    unittest.main()
