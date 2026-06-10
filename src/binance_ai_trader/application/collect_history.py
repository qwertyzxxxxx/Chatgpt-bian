from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from binance_ai_trader.capital import CapitalObservation
from binance_ai_trader.config import SectorConfig, UniverseConfig
from binance_ai_trader.domain.models import Contract, Kline, Ticker24h, UniverseMember
from binance_ai_trader.domain.universe import build_universe, is_leveraged_token
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository

LOGGER = logging.getLogger(__name__)
_INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class HistoricalCollectionResult:
    run_id: str
    symbols: tuple[str, ...]
    start_ms: int
    end_ms: int
    fetched_klines: int
    capital_observations: int
    universe_snapshots: int
    failures: tuple[str, ...]


class HistoricalDataCollector:
    """Resumable bootstrap using Binance USD-M public market-data endpoints only."""

    def __init__(
        self,
        client: BinancePublicClient,
        repository: MarketDataRepository,
        universe_config: UniverseConfig,
        sector_config: SectorConfig,
        request_pause_seconds: float = 0.05,
    ) -> None:
        if request_pause_seconds < 0:
            raise ValueError("request_pause_seconds cannot be negative")
        self._client = client
        self._repository = repository
        self._universe_config = universe_config
        self._sector_config = sector_config
        self._request_pause_seconds = request_pause_seconds

    def collect(self, days: int, end_ms: int | None = None) -> HistoricalCollectionResult:
        if not 1 <= days <= 3650:
            raise ValueError("days must be between 1 and 3650")
        cutoff_ms = end_ms if end_ms is not None else time.time_ns() // 1_000_000
        start_ms = cutoff_ms - days * _DAY_MS
        captured_at = _iso(cutoff_ms)
        run_id, snapshot_id = self._open_ingestion_run(start_ms, cutoff_ms, captured_at)
        failures: list[str] = []
        fetched_klines = 0
        capital_count = 0
        universe_count = 0
        symbols: tuple[str, ...] = ()
        try:
            contracts = self._client.exchange_info()
            tickers = self._client.tickers_24h()
            members = self._configured_members(contracts, tickers)
            symbols = tuple(item.symbol for item in members)
            for symbol in symbols:
                for interval in _INTERVAL_MS:
                    try:
                        fetched_klines += self._collect_klines(
                            symbol, interval, start_ms, cutoff_ms
                        )
                    except Exception as exc:  # isolate one public series and resume later
                        message = f"{symbol}/{interval}: {exc}"
                        failures.append(message)
                        LOGGER.warning("Historical kline collection failed: %s", message)
                try:
                    observations = self._collect_capital(
                        symbol, snapshot_id, start_ms, cutoff_ms
                    )
                    self._repository.save_capital_observations(observations, captured_at)
                    capital_count += len(observations)
                except Exception as exc:
                    message = f"{symbol}/capital: {exc}"
                    failures.append(message)
                    LOGGER.warning("Historical capital collection failed: %s", message)

            universe_count = self._save_daily_universes(
                members, start_ms, cutoff_ms
            )
            status = "PARTIAL" if failures else "SUCCEEDED"
            self._repository.finish_run(
                run_id, _utc_now(), status, len(symbols), fetched_klines,
                "; ".join(sorted(failures)) or None,
            )
            self._repository.finalize_snapshot(snapshot_id, _utc_now())
        except Exception as exc:
            self._repository.finish_run(
                run_id, _utc_now(), "FAILED", len(symbols), fetched_klines, str(exc)
            )
            snapshot = self._repository.load_snapshot(snapshot_id)
            if snapshot.finalized_at is None:
                self._repository.finalize_snapshot(snapshot_id, _utc_now())
            raise
        return HistoricalCollectionResult(
            run_id=run_id,
            symbols=symbols,
            start_ms=start_ms,
            end_ms=cutoff_ms,
            fetched_klines=fetched_klines,
            capital_observations=capital_count,
            universe_snapshots=universe_count,
            failures=tuple(sorted(failures)),
        )

    def _configured_members(
        self, contracts: tuple[Contract, ...], tickers: tuple[Ticker24h, ...]
    ) -> tuple[UniverseMember, ...]:
        discovered = {item.symbol: item for item in build_universe(
            contracts, tickers, self._universe_config
        )}
        contract_by_symbol = {item.symbol: item for item in contracts}
        ticker_by_symbol = {item.symbol: item for item in tickers}
        requested = set(discovered) | set(self._sector_config.symbol_to_sector) | {
            "BTCUSDT", "ETHUSDT"
        }
        for symbol in sorted(requested):
            if symbol in discovered:
                continue
            contract = contract_by_symbol.get(symbol)
            ticker = ticker_by_symbol.get(symbol)
            if contract is None or ticker is None or not self._eligible_contract(contract):
                continue
            discovered[symbol] = UniverseMember(contract, ticker)
        return tuple(discovered[symbol] for symbol in sorted(discovered))

    def _eligible_contract(self, contract: Contract) -> bool:
        return (
            contract.contract_type == "PERPETUAL"
            and contract.status == "TRADING"
            and contract.quote_asset == "USDT"
            and contract.margin_asset == "USDT"
            and contract.symbol not in self._universe_config.denied_symbols
            and contract.base_asset not in self._universe_config.stablecoin_base_assets
            and not is_leveraged_token(
                contract.base_asset, self._universe_config.leveraged_token_suffixes
            )
        )

    def _collect_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int
    ) -> int:
        interval_ms = _INTERVAL_MS[interval]
        # Re-read the requested range on resume; SQLite upserts make this idempotent
        # and repair an interrupted final page without assuming history is gap-free.
        cursor = start_ms
        fetched = 0
        while cursor <= end_ms:
            batch = self._client.historical_klines(
                symbol,
                interval,
                limit=1500,
                start_time_ms=cursor,
                end_time_ms=end_ms,
                now_ms=end_ms + 1,
            )
            batch = tuple(
                item for item in batch
                if cursor <= item.open_time_ms and item.close_time_ms <= end_ms
            )
            if not batch:
                break
            fetched += self._repository.save_klines(batch)
            next_cursor = batch[-1].open_time_ms + interval_ms
            if next_cursor <= cursor:
                raise RuntimeError("historical kline pagination did not advance")
            cursor = next_cursor
            self._pause()
        return fetched

    def _collect_capital(
        self, symbol: str, snapshot_id: str, start_ms: int, end_ms: int
    ) -> tuple[CapitalObservation, ...]:
        observations: list[CapitalObservation] = []
        for metric, fetch, limit, retention_ms in (
            ("OPEN_INTEREST", self._client.open_interest_history, 500, 30 * _DAY_MS),
            ("FUNDING_RATE", self._client.funding_rate_history, 1000, None),
            ("LONG_SHORT_RATIO", self._client.global_long_short_ratio_history, 500, 30 * _DAY_MS),
        ):
            # Binance documents limited retention for futures-data series. Start at
            # the oldest range the public endpoint can currently supply instead of
            # stopping on an empty request far before that range.
            cursor = max(start_ms, end_ms - retention_ms) if retention_ms else start_ms
            observations.extend(
                CapitalObservation(symbol, metric, timestamp, value, snapshot_id)
                for timestamp, value in self._paged_history(
                    fetch, symbol, cursor, end_ms, limit
                )
            )
        volume_start = start_ms
        observations.extend(
            CapitalObservation(symbol, "QUOTE_VOLUME_24H", timestamp, value, snapshot_id)
            for timestamp, value in self._quote_volume_history(symbol, start_ms, volume_start, end_ms)
        )
        return tuple(observations)

    def _paged_history(self, fetch, symbol: str, start_ms: int, end_ms: int, limit: int):
        rows = []
        cursor = start_ms
        while cursor <= end_ms:
            batch = tuple(fetch(symbol, limit, cursor, end_ms))
            batch = tuple(item for item in batch if cursor <= item[0] <= end_ms)
            if not batch:
                break
            rows.extend(batch)
            next_cursor = batch[-1][0] + 1
            if next_cursor <= cursor:
                raise RuntimeError("capital history pagination did not advance")
            cursor = next_cursor
            self._pause()
            if len(batch) < limit:
                break
        return tuple(dict.fromkeys(rows))

    def _quote_volume_history(
        self, symbol: str, history_start_ms: int, output_start_ms: int, end_ms: int
    ) -> tuple[tuple[int, Decimal], ...]:
        bars = self._repository.load_klines_range(
            symbol, "15m", history_start_ms, end_ms
        )
        window: deque[Kline] = deque()
        total = Decimal("0")
        rows = []
        for bar in bars:
            window.append(bar)
            total += bar.quote_volume
            while window and window[0].close_time_ms <= bar.close_time_ms - _DAY_MS:
                total -= window.popleft().quote_volume
            if (
                len(window) >= 96
                and bar.close_time_ms >= output_start_ms
                and (bar.close_time_ms + 1) % _INTERVAL_MS["1h"] == 0
            ):
                rows.append((bar.close_time_ms, total))
        return tuple(rows)

    def _save_daily_universes(
        self, members: tuple[UniverseMember, ...], start_ms: int, end_ms: int
    ) -> int:
        saved = 0
        first_day_end = ((start_ms // _DAY_MS) + 1) * _DAY_MS - 1
        for day_end in range(first_day_end, end_ms + 1, _DAY_MS):
            base_run_id = f"history-universe-{day_end}"
            existing_status = self._repository.collection_run_status(base_run_id)
            if existing_status == "SUCCEEDED":
                continue
            run_id = (
                base_run_id if existing_status is None
                else f"{base_run_id}-resume-{uuid4()}"
            )
            historical = []
            for member in members:
                bars = self._repository.load_klines_range(
                    member.symbol, "15m", day_end - _DAY_MS + 1, day_end
                )
                if len(bars) < 96:
                    continue
                quote_volume = sum((item.quote_volume for item in bars), Decimal("0"))
                if (
                    quote_volume <= self._universe_config.minimum_quote_volume_24h
                    and member.symbol not in {"BTCUSDT", "ETHUSDT"}
                ):
                    continue
                change = (
                    (bars[-1].close - bars[0].open) / bars[0].open * 100
                    if bars[0].open > 0 else Decimal("0")
                )
                historical.append(UniverseMember(
                    member.contract,
                    Ticker24h(member.symbol, quote_volume, change, day_end),
                ))
            if not historical:
                continue
            observed_at = _iso(day_end)
            self._repository.start_run(run_id, observed_at)
            self._repository.save_universe(run_id, historical, observed_at)
            self._repository.finish_run(
                run_id, observed_at, "SUCCEEDED", len(historical), 0, None
            )
            snapshot = self._repository.load_snapshot_for_run(run_id)
            self._repository.finalize_snapshot(snapshot.snapshot_id, observed_at)
            saved += 1
        return saved

    def _open_ingestion_run(
        self, start_ms: int, end_ms: int, created_at: str
    ) -> tuple[str, str]:
        base = f"history-ingest-{start_ms}-{end_ms}"
        run_id = base
        if self._repository.collection_run_exists(run_id):
            snapshot = self._repository.load_snapshot_for_run(run_id)
            if snapshot.finalized_at is None:
                return run_id, snapshot.snapshot_id
            run_id = f"{base}-resume-{uuid4()}"
        self._repository.start_run(run_id, created_at)
        return run_id, self._repository.load_snapshot_for_run(run_id).snapshot_id

    def _pause(self) -> None:
        if self._request_pause_seconds:
            time.sleep(self._request_pause_seconds)


def _iso(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, UTC).isoformat(timespec="milliseconds")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
