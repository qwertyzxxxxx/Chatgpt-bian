from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from uuid import uuid4

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.domain.models import CollectionResult, Kline
from binance_ai_trader.domain.universe import build_universe
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository

LOGGER = logging.getLogger(__name__)
INTERVALS = ("15m", "1h", "4h")


class MarketDataCollector:
    def __init__(
        self,
        client: BinancePublicClient,
        repository: MarketDataRepository,
        universe_config: UniverseConfig,
        kline_limit: int = 200,
        max_workers: int = 5,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._client = client
        self._repository = repository
        self._universe_config = universe_config
        self._kline_limit = kline_limit
        self._max_workers = max_workers

    def collect(self) -> CollectionResult:
        run_id = str(uuid4())
        started_at = _utc_now()
        self._repository.start_run(run_id, started_at)

        try:
            contracts = self._client.exchange_info()
            tickers = self._client.tickers_24h()
            universe = build_universe(contracts, tickers, self._universe_config)
            self._repository.save_universe(run_id, universe, _utc_now())

            kline_count, failures = self._collect_klines(tuple(item.symbol for item in universe))
            status = "PARTIAL" if failures else "SUCCEEDED"
            self._repository.finish_run(
                run_id=run_id,
                finished_at=_utc_now(),
                status=status,
                universe_size=len(universe),
                kline_count=kline_count,
                error_summary="; ".join(failures) or None,
            )
            return CollectionResult(run_id, universe, kline_count, tuple(failures))
        except Exception as exc:
            self._repository.finish_run(run_id, _utc_now(), "FAILED", 0, 0, str(exc))
            raise

    def _collect_klines(self, symbols: tuple[str, ...]) -> tuple[int, list[str]]:
        failures: list[str] = []
        count = 0
        with ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="binance-data") as executor:
            futures = {
                executor.submit(self._client.klines, symbol, interval, self._kline_limit): (symbol, interval)
                for symbol in symbols
                for interval in INTERVALS
            }
            for future in as_completed(futures):
                symbol, interval = futures[future]
                try:
                    klines: tuple[Kline, ...] = future.result()
                    count += self._repository.save_klines(klines)
                except Exception as exc:  # isolate one symbol/interval without aborting the scan
                    message = f"{symbol}/{interval}: {exc}"
                    LOGGER.warning("Kline collection failed: %s", message)
                    failures.append(message)
        failures.sort()
        return count, failures


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
