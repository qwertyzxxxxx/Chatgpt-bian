from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.domain.models import Contract, Kline, Ticker24h
from binance_ai_trader.entrypoints.cli import build_parser
from binance_ai_trader.hotlist import (
    HotlistWatchlist,
    HotlistWatchlistPolicy,
    HotlistWatchlistRepository,
    build_ai_hotlist_review_prompt,
    parse_ai_hotlist_review_response,
)


def contract(symbol: str) -> Contract:
    return Contract(
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        margin_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        price_precision=4,
        quantity_precision=3,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
    )


def candles(symbol: str, interval: str) -> tuple[Kline, ...]:
    count = 60 if interval == "15m" else 30
    duration = 900_000 if interval == "15m" else 3_600_000
    rows = []
    for index in range(count):
        close = Decimal("100") + Decimal(index) / Decimal("10")
        rows.append(
            Kline(
                symbol=symbol,
                interval=interval,
                open_time_ms=index * duration,
                close_time_ms=(index + 1) * duration - 1,
                open=close - Decimal("0.05"),
                high=close + Decimal("0.5"),
                low=close - Decimal("0.5"),
                close=close,
                volume=Decimal("100"),
                quote_volume=Decimal("1000000"),
                trade_count=100,
            )
        )
    return tuple(rows)


class FakePublicClient:
    def __init__(self, symbols: tuple[str, ...]) -> None:
        self.contracts = tuple(contract(symbol) for symbol in symbols)
        self.tickers = tuple(
            Ticker24h(symbol, Decimal("10000000"), Decimal(str(30 - index)), 1)
            for index, symbol in enumerate(symbols)
        )
        self.calls: list[str] = []

    def exchange_info(self) -> tuple[Contract, ...]:
        self.calls.append("exchange_info")
        return self.contracts

    def tickers_24h(self) -> tuple[Ticker24h, ...]:
        self.calls.append("tickers_24h")
        return self.tickers

    def klines(self, symbol: str, interval: str, limit: int = 200) -> tuple[Kline, ...]:
        self.calls.append(f"klines:{symbol}:{interval}")
        return candles(symbol, interval)[-limit:]


CONFIG = UniverseConfig(
    minimum_quote_volume_24h=Decimal("5000000"),
    stablecoin_base_assets=frozenset({"USDC"}),
    leveraged_token_suffixes=("UP", "DOWN", "BULL", "BEAR"),
    denied_symbols=frozenset(),
)
START = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)


class HotlistWatchlistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = HotlistWatchlistRepository(
            Path(self.tempdir.name) / "market.db"
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_review_cli_options(self) -> None:
        args = build_parser().parse_args([
            "hotlist",
            "review",
            "--gainers",
            "6",
            "--losers",
            "6",
            "--max-opportunities",
            "3",
            "--expiry-minutes",
            "60",
            "--max-ttl-minutes",
            "120",
            "--refresh-minutes",
            "15",
            "--min-rr",
            "2",
            "--max-stop-pct",
            "5",
            "--min-quote-volume",
            "5000000",
            "--database",
            "data/market_data.db",
        ])
        self.assertEqual("review", args.hotlist_command)
        self.assertEqual((6, 6, 3), (args.gainers, args.losers, args.max_opportunities))
        self.assertEqual((60, 120, 15), (
            args.expiry_minutes,
            args.max_ttl_minutes,
            args.refresh_minutes,
        ))

    def test_inserts_new_rows_and_updates_existing_after_refresh(self) -> None:
        client = FakePublicClient(("ALPHAUSDT", "BETAUSDT"))
        watchlist = self._watchlist(client)

        watchlist.review(START)
        inserted = self.repository.load("ALPHAUSDT")
        self.assertIsNotNone(inserted)
        self.assertEqual(1, inserted.observation_count)
        self.assertEqual("GAINER", inserted.source)
        self.assertEqual("ACTIVE", inserted.status)

        watchlist.review(START + timedelta(minutes=15))
        updated = self.repository.load("ALPHAUSDT")
        self.assertEqual(2, updated.observation_count)
        self.assertEqual("2026-06-14T12:15:00+00:00", updated.last_seen_at)

    def test_extends_expiry_and_caps_it_at_max_ttl(self) -> None:
        client = FakePublicClient(("ALPHAUSDT",))
        watchlist = self._watchlist(client)

        for minutes in (0, 15, 30, 45, 60, 75):
            watchlist.review(START + timedelta(minutes=minutes))

        item = self.repository.load("ALPHAUSDT")
        self.assertEqual("2026-06-14T14:00:00+00:00", item.expires_at)
        self.assertEqual(6, item.observation_count)

    def test_expires_stale_symbols(self) -> None:
        client = FakePublicClient(("ALPHAUSDT",))
        watchlist = self._watchlist(client)
        watchlist.review(START)
        client.tickers = ()

        watchlist.review(START + timedelta(minutes=61))

        self.assertEqual("EXPIRED", self.repository.load("ALPHAUSDT").status)

    def test_analyzes_active_pool_beyond_current_top_list_and_caps_top_three(self) -> None:
        symbols = ("AUSDT", "BUSDT", "CUSDT", "DUSDT")
        client = FakePublicClient(symbols)
        seed_watchlist = self._watchlist(
            client,
            HotlistWatchlistPolicy(
                gainers=4,
                losers=1,
                max_opportunities=3,
                max_stop_pct=Decimal("5"),
            ),
        )
        seed_watchlist.review(START)
        client.tickers = (
            Ticker24h("DUSDT", Decimal("10000000"), Decimal("40"), 1),
            Ticker24h("CUSDT", Decimal("10000000"), Decimal("5"), 1),
            Ticker24h("BUSDT", Decimal("10000000"), Decimal("4"), 1),
            Ticker24h("AUSDT", Decimal("10000000"), Decimal("3"), 1),
        )

        watchlist = self._watchlist(
            client,
            HotlistWatchlistPolicy(
                gainers=1,
                losers=1,
                max_opportunities=3,
                max_stop_pct=Decimal("5"),
            ),
        )
        plans = watchlist.review(START + timedelta(minutes=15))

        self.assertEqual(3, len(plans))
        self.assertIn("klines:AUSDT:15m", client.calls)
        self.assertIn("klines:AUSDT:1h", client.calls)
        self.assertTrue(
            all(
                call in {"exchange_info", "tickers_24h"} or call.startswith("klines:")
                for call in client.calls
            )
        )

    def test_ai_prompt_and_parser(self) -> None:
        client = FakePublicClient(("ALPHAUSDT", "BETAUSDT"))
        plans = self._watchlist(client).review(START)

        prompt = build_ai_hotlist_review_prompt(plans)
        first = plans[0]
        for expected in (
            first.symbol,
            first.direction,
            f"entry={first.suggested_limit_entry}",
            f"SL={first.stop_loss}",
            f"TP1={first.tp1}",
            f"TP2={first.tp2}",
            f"RR={first.rr}",
            f"expiry={first.expires_at}",
        ):
            self.assertIn(expected, prompt)

        decisions = parse_ai_hotlist_review_response(
            "ALPHAUSDT: APPROVED - setup aligned\nBETAUSDT: REJECTED - weak retest"
        )
        self.assertEqual(("ALPHAUSDT", True), (decisions[0].symbol, decisions[0].approved))
        self.assertEqual(("BETAUSDT", False), (decisions[1].symbol, decisions[1].approved))

    def _watchlist(
        self,
        client: FakePublicClient,
        policy: HotlistWatchlistPolicy = HotlistWatchlistPolicy(),
    ) -> HotlistWatchlist:
        return HotlistWatchlist(client, self.repository, CONFIG, policy)


if __name__ == "__main__":
    unittest.main()
