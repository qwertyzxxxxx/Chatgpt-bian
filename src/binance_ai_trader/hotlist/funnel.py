from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.hotlist.models import HotlistCandidate
from binance_ai_trader.hotlist.repository import HotlistWatchlistRepository
from binance_ai_trader.hotlist.service import HotlistWatcher, HotlistWatcherPolicy, PublicMarketData


@dataclass(frozen=True, slots=True)
class HotlistFunnelPolicy:
    min_move_pct: Decimal = Decimal("15")
    min_quote_volume: Decimal = Decimal("5000000")
    min_rr: Decimal = Decimal("2")
    max_stop_pct: Decimal = Decimal("5")
    max_opportunities: int = 3

    def __post_init__(self) -> None:
        if self.min_move_pct < 0 or self.min_quote_volume < 0:
            raise ValueError("funnel thresholds cannot be negative")
        if self.min_rr < 1 or self.max_stop_pct <= 0:
            raise ValueError("invalid funnel opportunity thresholds")
        if self.max_opportunities < 1:
            raise ValueError("max_opportunities must be positive")


@dataclass(frozen=True, slots=True)
class FunnelStep:
    label: str
    count: int
    dropped: int
    drop_off_pct: float


@dataclass(frozen=True, slots=True)
class RejectedSymbol:
    symbol: str
    reason: str
    detail: str


@dataclass
class HotlistFunnelReport:
    generated_at: str
    parameters: dict
    steps: list[FunnelStep]
    top_rejections: list[RejectedSymbol]
    final_opportunities: list[str]
    research_only: bool = True


_REJECTION_PRIORITY: dict[str, int] = {
    "stop_too_wide": 0,
    "rr_below_min": 1,
    "insufficient_klines": 2,
    "no_ticker_data": 3,
    "low_volume": 4,
    "low_move": 5,
    "no_ticker": 6,
    "stablecoin": 7,
    "denied": 8,
    "leveraged_token": 9,
}


class HotlistFunnelAnalyzer:
    """Read-only diagnostic funnel — explains why no signals are generated."""

    def __init__(
        self,
        client: PublicMarketData,
        repository: HotlistWatchlistRepository,
        universe_config: UniverseConfig,
        policy: HotlistFunnelPolicy = HotlistFunnelPolicy(),
    ) -> None:
        self._client = client
        self._repository = repository
        self._universe_config = universe_config
        self._policy = policy

    def run(self, now: datetime | None = None) -> HotlistFunnelReport:
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)

        # Stage 1: universe total
        all_contracts = self._client.exchange_info()
        universe_total = len(all_contracts)

        # Stage 2: USDT perpetuals (TRADING)
        usdt_perps = [
            c for c in all_contracts
            if c.quote_asset == "USDT"
            and c.margin_asset == "USDT"
            and c.contract_type == "PERPETUAL"
            and c.status == "TRADING"
        ]
        usdt_perp_count = len(usdt_perps)

        # Stage 3: after exclusions (stablecoin / denied / leveraged)
        exclusion_rejected: list[RejectedSymbol] = []
        valid_symbols: set[str] = set()
        for c in usdt_perps:
            if c.base_asset in self._universe_config.stablecoin_base_assets:
                exclusion_rejected.append(
                    RejectedSymbol(c.symbol, "stablecoin", f"base={c.base_asset}")
                )
            elif c.symbol in self._universe_config.denied_symbols:
                exclusion_rejected.append(
                    RejectedSymbol(c.symbol, "denied", f"symbol={c.symbol}")
                )
            elif c.base_asset.endswith(self._universe_config.leveraged_token_suffixes):
                exclusion_rejected.append(
                    RejectedSymbol(c.symbol, "leveraged_token", f"base={c.base_asset}")
                )
            else:
                valid_symbols.add(c.symbol)
        after_exclusions_count = len(valid_symbols)

        # Fetch tickers (filter to valid symbols only)
        tickers = {
            t.symbol: t for t in self._client.tickers_24h() if t.symbol in valid_symbols
        }

        # Stage 4: 24h move >= min_move_pct
        move_passed = []
        move_rejected: list[RejectedSymbol] = []
        for symbol in sorted(valid_symbols):
            ticker = tickers.get(symbol)
            if ticker is None:
                move_rejected.append(
                    RejectedSymbol(symbol, "no_ticker", "not in tickers_24h")
                )
                continue
            if abs(ticker.price_change_percent) >= self._policy.min_move_pct:
                move_passed.append(ticker)
            else:
                move_rejected.append(
                    RejectedSymbol(
                        symbol,
                        "low_move",
                        f"change={float(ticker.price_change_percent):+.1f}% < {self._policy.min_move_pct}%",
                    )
                )
        move_pass_count = len(move_passed)

        # Stage 5: quote_volume >= min_quote_volume
        volume_passed = []
        volume_rejected: list[RejectedSymbol] = []
        for ticker in move_passed:
            if ticker.quote_volume >= self._policy.min_quote_volume:
                volume_passed.append(ticker)
            else:
                volume_rejected.append(
                    RejectedSymbol(
                        ticker.symbol,
                        "low_volume",
                        f"vol={float(ticker.quote_volume):,.0f} < {float(self._policy.min_quote_volume):,.0f}",
                    )
                )
        volume_pass_count = len(volume_passed)

        # Stage 6: gainers / Stage 7: losers (subsets of volume_passed)
        gainers = [t for t in volume_passed if t.price_change_percent > 0]
        losers = [t for t in volume_passed if t.price_change_percent < 0]
        gainers_count = len(gainers)
        losers_count = len(losers)
        combined_pool = gainers_count + losers_count

        # Stage 8: watchlist active (read-only)
        active_items = self._repository.active()
        active_count = len(active_items)

        # Stage 9-12: technical planning (no DB writes)
        watcher = HotlistWatcher(
            self._client,
            self._universe_config,
            HotlistWatcherPolicy(
                limit=5,
                min_move_pct=Decimal("0"),
                min_quote_volume=self._policy.min_quote_volume,
                expiry_minutes=60,
            ),
        )
        review_plans = []
        kline_rejected: list[RejectedSymbol] = []
        rr_rejected: list[RejectedSymbol] = []
        stop_rejected: list[RejectedSymbol] = []

        for item in active_items:
            ticker = tickers.get(item.symbol)
            if ticker is None or ticker.quote_volume < self._policy.min_quote_volume:
                kline_rejected.append(
                    RejectedSymbol(
                        item.symbol, "no_ticker_data", "not in current scan tickers"
                    )
                )
                continue
            candidate = HotlistCandidate(
                symbol=item.symbol,
                direction="LONG" if item.source == "GAINER" else "SHORT",
                change_24h_pct=ticker.price_change_percent,
                quote_volume=ticker.quote_volume,
            )
            plan = watcher.plan_candidate(candidate, generated_at)
            if plan is None:
                kline_rejected.append(
                    RejectedSymbol(
                        item.symbol, "insufficient_klines", "not enough kline data"
                    )
                )
                continue
            review_plans.append(plan)

        review_count = len(review_plans)

        rr_pass = []
        for plan in review_plans:
            if plan.rr < self._policy.min_rr:
                rr_rejected.append(
                    RejectedSymbol(
                        plan.symbol,
                        "rr_below_min",
                        f"rr={float(plan.rr):.2f} < {self._policy.min_rr}",
                    )
                )
            else:
                rr_pass.append(plan)
        rr_pass_count = len(rr_pass)

        stop_pass = []
        for plan in rr_pass:
            stop_pct = (
                abs(plan.suggested_limit_entry - plan.stop_loss)
                / plan.suggested_limit_entry
                * 100
            )
            if stop_pct > self._policy.max_stop_pct:
                stop_rejected.append(
                    RejectedSymbol(
                        plan.symbol,
                        "stop_too_wide",
                        f"stop_pct={float(stop_pct):.1f}% > {self._policy.max_stop_pct}%",
                    )
                )
            else:
                stop_pass.append(plan)
        stop_pass_count = len(stop_pass)

        stop_pass.sort(key=lambda p: (-abs(p.change_24h_pct), -p.quote_volume, p.symbol))
        final = stop_pass[: self._policy.max_opportunities]
        final_count = len(final)

        # Build steps with explicit previous for drop-off calculation
        raw = [
            ("universe_total",             universe_total,          None),
            ("usdt_perpetual",             usdt_perp_count,         universe_total),
            ("after_exclusions",           after_exclusions_count,  usdt_perp_count),
            ("move_ge_min_move",           move_pass_count,         after_exclusions_count),
            ("volume_ge_min_quote_volume", volume_pass_count,       move_pass_count),
            ("gainers",                    gainers_count,           volume_pass_count),
            ("losers",                     losers_count,            volume_pass_count),
            ("watchlist_active",           active_count,            combined_pool),
            ("review_candidates",          review_count,            active_count),
            ("rr_pass",                    rr_pass_count,           review_count),
            ("stop_pass",                  stop_pass_count,         rr_pass_count),
            ("final_opportunities",        final_count,             stop_pass_count),
        ]
        steps = []
        for label, count, prev in raw:
            if prev is None:
                steps.append(FunnelStep(label=label, count=count, dropped=0, drop_off_pct=0.0))
            else:
                dropped = max(0, prev - count)
                drop_off_pct = round(dropped / prev * 100, 1) if prev > 0 else 0.0
                steps.append(
                    FunnelStep(label=label, count=count, dropped=dropped, drop_off_pct=drop_off_pct)
                )

        # Top 10 rejections: later-stage first (highest diagnostic value)
        all_rejected = (
            stop_rejected
            + rr_rejected
            + kline_rejected
            + volume_rejected
            + move_rejected
            + exclusion_rejected
        )
        top_rejections = sorted(
            all_rejected, key=lambda r: _REJECTION_PRIORITY.get(r.reason, 99)
        )[:10]

        return HotlistFunnelReport(
            generated_at=generated_at.isoformat(timespec="seconds"),
            parameters={
                "min_move_pct": str(self._policy.min_move_pct),
                "min_quote_volume": str(self._policy.min_quote_volume),
                "min_rr": str(self._policy.min_rr),
                "max_stop_pct": str(self._policy.max_stop_pct),
            },
            steps=steps,
            top_rejections=top_rejections,
            final_opportunities=[p.symbol for p in final],
        )
