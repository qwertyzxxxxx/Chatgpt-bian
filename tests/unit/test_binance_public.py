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

    def test_maps_public_capital_flow_endpoints(self) -> None:
        client = StubClient({
            "/fapi/v1/openInterest": {"openInterest": "123.45"},
            "/futures/data/openInterestHist": [
                {"timestamp": 1, "sumOpenInterest": "100"},
                {"timestamp": 2, "sumOpenInterest": "110"},
            ],
            "/fapi/v1/premiumIndex": {"lastFundingRate": "0.0001"},
            "/fapi/v1/fundingRate": [
                {"fundingTime": 2, "fundingRate": "0.0002"}
            ],
            "/futures/data/globalLongShortAccountRatio": [
                {"timestamp": 2, "longShortRatio": "1.2"}
            ],
        })
        self.assertEqual("123.45", str(client.open_interest("BTCUSDT")))
        self.assertEqual((2, "110"), (client.open_interest_history("BTCUSDT")[-1][0],
                                      str(client.open_interest_history("BTCUSDT")[-1][1])))
        self.assertEqual("0.0001", str(client.current_funding_rate("BTCUSDT")))
        self.assertEqual((2, "0.0002"), (
            client.funding_rate_history("BTCUSDT")[-1][0],
            str(client.funding_rate_history("BTCUSDT")[-1][1]),
        ))
        self.assertEqual((2, "1.2"), (
            client.global_long_short_ratio_history("BTCUSDT")[-1][0],
            str(client.global_long_short_ratio_history("BTCUSDT")[-1][1]),
        ))
        self.assertEqual("1.2", str(client.global_long_short_ratio("BTCUSDT")))

    def test_historical_klines_passes_public_time_range(self) -> None:
        class CapturingClient(StubClient):
            def __init__(self):
                super().__init__({"/fapi/v1/klines": fixture("klines.json")})
                self.params = None

            def _get_json(self, path, params=None):
                self.params = params
                return self.payloads[path]

        client = CapturingClient()
        client.historical_klines(
            "BTCUSDT", "15m", limit=1000,
            start_time_ms=1710000000000, end_time_ms=1710002699999,
            now_ms=1710002700000,
        )

        self.assertEqual("1710000000000", client.params["startTime"])
        self.assertEqual("1710002699999", client.params["endTime"])
        self.assertEqual("1000", client.params["limit"])
        self.assertNotIn("apiKey", client.params)

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
