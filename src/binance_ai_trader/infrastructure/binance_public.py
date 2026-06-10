from __future__ import annotations

import json
import random
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from binance_ai_trader.domain.models import Contract, Kline, Ticker24h


class BinancePublicApiError(RuntimeError):
    pass


class BinancePublicClient:
    """Client for Binance USD-M Futures public market-data endpoints only."""

    def __init__(
        self,
        base_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    def exchange_info(self) -> tuple[Contract, ...]:
        payload = self._get_json("/fapi/v1/exchangeInfo")
        try:
            return tuple(self._parse_contract(item) for item in payload["symbols"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise BinancePublicApiError("Invalid exchangeInfo response") from exc

    def tickers_24h(self) -> tuple[Ticker24h, ...]:
        payload = self._get_json("/fapi/v1/ticker/24hr")
        if not isinstance(payload, list):
            raise BinancePublicApiError("Expected ticker array")
        try:
            return tuple(
                Ticker24h(
                    symbol=item["symbol"],
                    quote_volume=Decimal(item["quoteVolume"]),
                    price_change_percent=Decimal(item["priceChangePercent"]),
                    close_time_ms=int(item["closeTime"]),
                )
                for item in payload
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise BinancePublicApiError("Invalid 24h ticker response") from exc

    def open_interest(self, symbol: str) -> Decimal:
        payload = self._get_json("/fapi/v1/openInterest", {"symbol": symbol})
        try:
            return Decimal(payload["openInterest"])
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise BinancePublicApiError(f"Invalid open interest response for {symbol}") from exc

    def open_interest_history(
        self, symbol: str, limit: int = 30, start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> tuple[tuple[int, Decimal], ...]:
        params = {"symbol": symbol, "period": "1h", "limit": str(limit)}
        if start_time_ms is not None:
            params["startTime"] = str(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = str(end_time_ms)
        payload = self._get_json("/futures/data/openInterestHist", params)
        if not isinstance(payload, list):
            raise BinancePublicApiError("Expected open interest history array")
        try:
            return tuple((int(item["timestamp"]), Decimal(item["sumOpenInterest"])) for item in payload)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise BinancePublicApiError(f"Invalid open interest history for {symbol}") from exc

    def funding_rate_history(
        self, symbol: str, limit: int = 100, start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> tuple[tuple[int, Decimal], ...]:
        params = {"symbol": symbol, "limit": str(limit)}
        if start_time_ms is not None:
            params["startTime"] = str(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = str(end_time_ms)
        payload = self._get_json("/fapi/v1/fundingRate", params)
        if not isinstance(payload, list):
            raise BinancePublicApiError("Expected funding history array")
        try:
            return tuple((int(item["fundingTime"]), Decimal(item["fundingRate"])) for item in payload)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise BinancePublicApiError(f"Invalid funding history for {symbol}") from exc

    def global_long_short_ratio_history(
        self, symbol: str, limit: int = 30, start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> tuple[tuple[int, Decimal], ...]:
        params = {"symbol": symbol, "period": "1h", "limit": str(limit)}
        if start_time_ms is not None:
            params["startTime"] = str(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = str(end_time_ms)
        payload = self._get_json("/futures/data/globalLongShortAccountRatio", params)
        if not isinstance(payload, list):
            raise BinancePublicApiError("Expected global long/short ratio array")
        try:
            return tuple((int(item["timestamp"]), Decimal(item["longShortRatio"])) for item in payload)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise BinancePublicApiError(f"Invalid global long/short ratio history for {symbol}") from exc

    def current_funding_rate(self, symbol: str) -> Decimal:
        payload = self._get_json("/fapi/v1/premiumIndex", {"symbol": symbol})
        try:
            return Decimal(payload["lastFundingRate"])
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise BinancePublicApiError(f"Invalid funding response for {symbol}") from exc

    def global_long_short_ratio(self, symbol: str) -> Decimal:
        payload = self._get_json(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": "1h", "limit": "1"},
        )
        if not isinstance(payload, list) or not payload:
            raise BinancePublicApiError(f"Missing global long/short ratio for {symbol}")
        try:
            return Decimal(payload[-1]["longShortRatio"])
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise BinancePublicApiError(f"Invalid global long/short ratio for {symbol}") from exc

    def klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        now_ms: int | None = None,
    ) -> tuple[Kline, ...]:
        if interval not in {"15m", "1h", "4h"}:
            raise ValueError(f"Unsupported interval: {interval}")
        if not 1 <= limit <= 1500:
            raise ValueError("Kline limit must be between 1 and 1500")
        payload = self._get_json(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": str(limit)},
        )
        if not isinstance(payload, list):
            raise BinancePublicApiError("Expected kline array")
        current_ms = now_ms if now_ms is not None else time.time_ns() // 1_000_000
        try:
            parsed = tuple(
                Kline(
                    symbol=symbol,
                    interval=interval,
                    open_time_ms=int(row[0]),
                    open=Decimal(row[1]),
                    high=Decimal(row[2]),
                    low=Decimal(row[3]),
                    close=Decimal(row[4]),
                    volume=Decimal(row[5]),
                    close_time_ms=int(row[6]),
                    quote_volume=Decimal(row[7]),
                    trade_count=int(row[8]),
                )
                for row in payload
                if int(row[6]) < current_ms
            )
        except (IndexError, TypeError, ValueError, InvalidOperation) as exc:
            raise BinancePublicApiError(f"Invalid {interval} kline response for {symbol}") from exc
        self._validate_klines(parsed, symbol, interval)
        return parsed

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self._base_url}{path}{query}"
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "binance-ai-trader/0.1"})
        for attempt in range(self._max_retries + 1):
            try:
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == self._max_retries:
                    raise BinancePublicApiError(f"Binance returned HTTP {exc.code} for {path}") from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == self._max_retries:
                    raise BinancePublicApiError(f"Binance request failed for {path}: {exc}") from exc
            delay = min(0.5 * (2**attempt), 8.0) + random.uniform(0.0, 0.25)
            time.sleep(delay)
        raise AssertionError("retry loop exhausted")

    @staticmethod
    def _parse_contract(item: dict[str, Any]) -> Contract:
        filters = {entry["filterType"]: entry for entry in item["filters"]}
        return Contract(
            symbol=item["symbol"],
            base_asset=item["baseAsset"],
            quote_asset=item["quoteAsset"],
            margin_asset=item["marginAsset"],
            contract_type=item["contractType"],
            status=item["status"],
            price_precision=int(item["pricePrecision"]),
            quantity_precision=int(item["quantityPrecision"]),
            tick_size=Decimal(filters["PRICE_FILTER"]["tickSize"]),
            step_size=Decimal(filters["LOT_SIZE"]["stepSize"]),
        )

    @staticmethod
    def _validate_klines(klines: tuple[Kline, ...], symbol: str, interval: str) -> None:
        interval_ms = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}[interval]
        previous: Kline | None = None
        for kline in klines:
            if min(kline.open, kline.high, kline.low, kline.close) <= 0:
                raise BinancePublicApiError(f"Non-positive OHLC value for {symbol} {interval}")
            if kline.high < max(kline.open, kline.close) or kline.low > min(kline.open, kline.close):
                raise BinancePublicApiError(f"Invalid OHLC range for {symbol} {interval}")
            if previous and kline.open_time_ms - previous.open_time_ms != interval_ms:
                raise BinancePublicApiError(f"Kline gap or duplicate for {symbol} {interval}")
            previous = kline
