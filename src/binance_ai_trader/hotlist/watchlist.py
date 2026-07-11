from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.hotlist.models import (
    HotlistCandidate,
    HotlistEntryPlan,
    HotlistWatchlistItem,
)
from binance_ai_trader.hotlist.repository import HotlistWatchlistRepository
from binance_ai_trader.hotlist.service import HotlistWatcher, HotlistWatcherPolicy, PublicMarketData


@dataclass(frozen=True, slots=True)
class HotlistWatchlistPolicy:
    gainers: int = 6
    losers: int = 6
    max_opportunities: int = 3
    expiry_minutes: int = 60
    max_ttl_minutes: int = 120
    refresh_minutes: int = 15
    min_rr: Decimal = Decimal("2")
    max_stop_pct: Decimal = Decimal("5")
    min_quote_volume: Decimal = Decimal("5000000")
    min_move_pct: Decimal = Decimal("0")
    min_volume_ratio: Decimal = Decimal("0")
    require_trend_aligned_1h: bool = False
    require_trend_aligned_4h: bool = False

    def __post_init__(self) -> None:
        if min(self.gainers, self.losers, self.max_opportunities) < 1:
            raise ValueError("hotlist counts must be positive")
        if self.max_opportunities > 3:
            raise ValueError("max_opportunities cannot exceed 3")
        if min(self.expiry_minutes, self.max_ttl_minutes, self.refresh_minutes) < 1:
            raise ValueError("hotlist durations must be positive")
        if self.expiry_minutes > self.max_ttl_minutes:
            raise ValueError("expiry_minutes cannot exceed max_ttl_minutes")
        if self.min_rr < 1 or self.max_stop_pct <= 0 or self.min_quote_volume < 0:
            raise ValueError("invalid opportunity thresholds")
        if self.min_move_pct < 0 or self.min_volume_ratio < 0:
            raise ValueError("min_move_pct and min_volume_ratio cannot be negative")


class HotlistWatchlist:
    """Maintain and analyze a rolling, public-data-only observation pool."""

    def __init__(
        self,
        client: PublicMarketData,
        repository: HotlistWatchlistRepository,
        universe_config: UniverseConfig,
        policy: HotlistWatchlistPolicy = HotlistWatchlistPolicy(),
    ) -> None:
        self._client = client
        self._repository = repository
        self._universe_config = universe_config
        self._policy = policy

    def review(self, now: datetime | None = None) -> tuple[HotlistEntryPlan, ...]:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        observed_iso = observed_at.isoformat(timespec="seconds")
        self._repository.expire_before(observed_iso)
        tickers = {item.symbol: item for item in self._client.tickers_24h()}
        valid = {
            item.symbol
            for item in self._client.exchange_info()
            if item.quote_asset == "USDT"
            and item.margin_asset == "USDT"
            and item.contract_type == "PERPETUAL"
            and item.status == "TRADING"
            and item.base_asset not in self._universe_config.stablecoin_base_assets
            and item.symbol not in self._universe_config.denied_symbols
            and not item.base_asset.endswith(self._universe_config.leveraged_token_suffixes)
        }
        eligible = [
            item
            for item in tickers.values()
            if item.symbol in valid and item.quote_volume >= self._policy.min_quote_volume
        ]
        gainers = sorted(
            (item for item in eligible if item.price_change_percent > 0),
            key=lambda item: (-item.price_change_percent, -item.quote_volume, item.symbol),
        )[: self._policy.gainers]
        losers = sorted(
            (item for item in eligible if item.price_change_percent < 0),
            key=lambda item: (item.price_change_percent, -item.quote_volume, item.symbol),
        )[: self._policy.losers]
        for source, ranked in (("GAINER", gainers), ("LOSER", losers)):
            for rank, ticker in enumerate(ranked, start=1):
                self._observe(ticker.symbol, source, rank, observed_at)

        watcher = HotlistWatcher(
            self._client,
            self._universe_config,
            HotlistWatcherPolicy(
                limit=5,
                min_move_pct=Decimal("0"),
                min_quote_volume=self._policy.min_quote_volume,
                expiry_minutes=self._policy.expiry_minutes,
            ),
        )
        plans = []
        for item in self._repository.active():
            ticker = tickers.get(item.symbol)
            if ticker is None or ticker.quote_volume < self._policy.min_quote_volume:
                continue
            if self._policy.min_move_pct > 0 and abs(ticker.price_change_percent) < self._policy.min_move_pct:
                continue
            candidate = HotlistCandidate(
                symbol=item.symbol,
                direction="LONG" if item.source == "GAINER" else "SHORT",
                change_24h_pct=ticker.price_change_percent,
                quote_volume=ticker.quote_volume,
            )
            plan = watcher.plan_candidate(
                candidate, observed_at,
                fetch_4h=self._policy.require_trend_aligned_4h,
            )
            if plan is None or plan.rr < self._policy.min_rr:
                continue
            stop_pct = abs(plan.suggested_limit_entry - plan.stop_loss) / plan.suggested_limit_entry * 100
            if stop_pct > self._policy.max_stop_pct:
                continue
            if self._policy.min_volume_ratio > 0 and plan.volume_ratio_15m < self._policy.min_volume_ratio:
                continue
            if self._policy.require_trend_aligned_1h and not plan.trend_aligned:
                continue
            if self._policy.require_trend_aligned_4h and not plan.trend_4h_aligned:
                continue
            plans.append(plan)
        plans.sort(
            key=lambda item: (-abs(item.change_24h_pct), -item.quote_volume, item.symbol)
        )
        return tuple(plans[: self._policy.max_opportunities])

    def _observe(
        self, symbol: str, source: str, rank: int, observed_at: datetime
    ) -> None:
        existing = self._repository.load(symbol)
        if existing is None or existing.status == "EXPIRED":
            self._repository.save(
                HotlistWatchlistItem(
                    symbol=symbol,
                    source=source,
                    first_seen_at=observed_at.isoformat(timespec="seconds"),
                    last_seen_at=observed_at.isoformat(timespec="seconds"),
                    expires_at=(observed_at + timedelta(minutes=self._policy.expiry_minutes)).isoformat(
                        timespec="seconds"
                    ),
                    observation_count=1,
                    last_rank=rank,
                    status="ACTIVE",
                )
            )
            return
        last_seen = datetime.fromisoformat(existing.last_seen_at)
        if observed_at - last_seen < timedelta(minutes=self._policy.refresh_minutes):
            return
        first_seen = datetime.fromisoformat(existing.first_seen_at)
        maximum_expiry = first_seen + timedelta(minutes=self._policy.max_ttl_minutes)
        extended_expiry = min(
            observed_at + timedelta(minutes=self._policy.expiry_minutes),
            maximum_expiry,
        )
        self._repository.save(
            HotlistWatchlistItem(
                symbol=symbol,
                source=source,
                first_seen_at=existing.first_seen_at,
                last_seen_at=observed_at.isoformat(timespec="seconds"),
                expires_at=extended_expiry.isoformat(timespec="seconds"),
                observation_count=existing.observation_count + 1,
                last_rank=rank,
                status="ACTIVE",
            )
        )
