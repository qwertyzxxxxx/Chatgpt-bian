from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.domain.models import Contract, Kline, Ticker24h
from binance_ai_trader.entrypoints.cli import build_parser
from binance_ai_trader.hotlist.funnel import (
    FunnelStep,
    HotlistFunnelAnalyzer,
    HotlistFunnelPolicy,
    HotlistFunnelReport,
    RejectedSymbol,
)
from binance_ai_trader.hotlist.models import HotlistWatchlistItem
from binance_ai_trader.hotlist.reporting import render_hotlist_funnel


def _contract(symbol: str, base: str | None = None, status: str = "TRADING") -> Contract:
    return Contract(
        symbol=symbol,
        base_asset=base or symbol.removesuffix("USDT"),
        quote_asset="USDT",
        margin_asset="USDT",
        contract_type="PERPETUAL",
        status=status,
        price_precision=4,
        quantity_precision=3,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
    )


def _klines(symbol: str, interval: str, count: int = 60) -> tuple[Kline, ...]:
    duration = 900_000 if interval == "15m" else 3_600_000
    rows = []
    for i in range(count):
        close = Decimal("100") + Decimal(i)
        rows.append(
            Kline(
                symbol=symbol,
                interval=interval,
                open_time_ms=i * duration,
                close_time_ms=(i + 1) * duration - 1,
                open=close - Decimal("0.5"),
                high=close + Decimal("2"),
                low=close - Decimal("2"),
                close=close,
                volume=Decimal("100"),
                quote_volume=Decimal("6000000"),
                trade_count=100,
            )
        )
    return tuple(rows)


CONFIG = UniverseConfig(
    minimum_quote_volume_24h=Decimal("5000000"),
    stablecoin_base_assets=frozenset({"USDC", "BUSD"}),
    leveraged_token_suffixes=("UP", "DOWN", "BULL", "BEAR"),
    denied_symbols=frozenset({"DENIEDUSDT"}),
)


class FakeClient:
    def __init__(
        self,
        contracts: tuple[Contract, ...],
        tickers: tuple[Ticker24h, ...],
        market: dict[tuple[str, str], tuple[Kline, ...]] | None = None,
    ) -> None:
        self._contracts = contracts
        self._tickers = tickers
        self._market = market or {}

    def exchange_info(self) -> tuple[Contract, ...]:
        return self._contracts

    def tickers_24h(self) -> tuple[Ticker24h, ...]:
        return self._tickers

    def klines(self, symbol: str, interval: str, limit: int = 200) -> tuple[Kline, ...]:
        return self._market.get((symbol, interval), ())[-limit:]


class FakeRepository:
    def __init__(self, active_items: tuple[HotlistWatchlistItem, ...] = ()) -> None:
        self._active = active_items

    def active(self) -> tuple[HotlistWatchlistItem, ...]:
        return self._active


def _active_item(symbol: str, source: str = "GAINER") -> HotlistWatchlistItem:
    return HotlistWatchlistItem(
        symbol=symbol,
        source=source,
        first_seen_at="2026-06-15T08:00:00+00:00",
        last_seen_at="2026-06-15T08:00:00+00:00",
        expires_at="2026-06-15T10:00:00+00:00",
        observation_count=1,
        last_rank=1,
        status="ACTIVE",
    )


class HotlistFunnelAnalyzerTest(unittest.TestCase):

    def _make_client_and_repo(
        self,
        extra_contracts: tuple[Contract, ...] = (),
        extra_tickers: tuple[Ticker24h, ...] = (),
        active_items: tuple[HotlistWatchlistItem, ...] = (),
        market: dict | None = None,
    ):
        base_contracts = (
            _contract("ALPHAUSDT"),
            _contract("BETAUSDT"),
            _contract("GAMMAUSDT"),
            _contract("USDCUSDT", "USDC"),
            _contract("DENIEDUSDT"),
            _contract("OLDFUTURESUSDT", status="BREAK"),
        ) + extra_contracts
        base_tickers = (
            Ticker24h("ALPHAUSDT", Decimal("8000000"), Decimal("25"), 1),
            Ticker24h("BETAUSDT", Decimal("7000000"), Decimal("-20"), 1),
            Ticker24h("GAMMAUSDT", Decimal("3000000"), Decimal("20"), 1),
            Ticker24h("USDCUSDT", Decimal("9000000"), Decimal("30"), 1),
            Ticker24h("DENIEDUSDT", Decimal("9000000"), Decimal("30"), 1),
        ) + extra_tickers
        client = FakeClient(base_contracts, base_tickers, market)
        repo = FakeRepository(active_items)
        return client, repo

    def test_funnel_step_count(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        self.assertEqual(12, len(report.steps))

    def test_universe_total_counts_all_contracts(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        step = {s.label: s for s in report.steps}
        self.assertEqual(6, step["universe_total"].count)

    def test_usdt_perpetual_excludes_non_trading(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        step = {s.label: s for s in report.steps}
        self.assertEqual(5, step["usdt_perpetual"].count)

    def test_after_exclusions_removes_stablecoin_and_denied(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        step = {s.label: s for s in report.steps}
        self.assertEqual(3, step["after_exclusions"].count)

    def test_move_filter_removes_low_movers(self) -> None:
        client, repo = self._make_client_and_repo(
            extra_contracts=(_contract("LOWUSDT"),),
            extra_tickers=(Ticker24h("LOWUSDT", Decimal("9000000"), Decimal("5"), 1),),
        )
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        step = {s.label: s for s in report.steps}
        self.assertLess(step["move_ge_min_move"].count, step["after_exclusions"].count)

    def test_volume_filter_removes_low_volume(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        step = {s.label: s for s in report.steps}
        # GAMMAUSDT has volume 3M < 5M min_quote_volume, so should be dropped
        self.assertLess(
            step["volume_ge_min_quote_volume"].count,
            step["move_ge_min_move"].count,
        )

    def test_gainers_and_losers_are_subsets_of_volume_pass(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        step = {s.label: s for s in report.steps}
        self.assertLessEqual(step["gainers"].count, step["volume_ge_min_quote_volume"].count)
        self.assertLessEqual(step["losers"].count, step["volume_ge_min_quote_volume"].count)

    def test_watchlist_active_reflects_repository(self) -> None:
        client, repo = self._make_client_and_repo(
            active_items=(_active_item("ALPHAUSDT"), _active_item("BETAUSDT", "LOSER"))
        )
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        step = {s.label: s for s in report.steps}
        self.assertEqual(2, step["watchlist_active"].count)

    def test_empty_watchlist_zeroes_downstream_steps(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        step = {s.label: s for s in report.steps}
        self.assertEqual(0, step["watchlist_active"].count)
        self.assertEqual(0, step["review_candidates"].count)
        self.assertEqual(0, step["rr_pass"].count)
        self.assertEqual(0, step["stop_pass"].count)
        self.assertEqual(0, step["final_opportunities"].count)

    def test_review_candidates_requires_klines(self) -> None:
        market = {
            ("ALPHAUSDT", "15m"): _klines("ALPHAUSDT", "15m"),
            ("ALPHAUSDT", "1h"): _klines("ALPHAUSDT", "1h", 30),
        }
        client, repo = self._make_client_and_repo(
            active_items=(
                _active_item("ALPHAUSDT"),
                _active_item("BETAUSDT", "LOSER"),
            ),
            market=market,
        )
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        step = {s.label: s for s in report.steps}
        self.assertEqual(2, step["watchlist_active"].count)
        self.assertEqual(1, step["review_candidates"].count)

    def test_final_opportunities_capped_at_max_opportunities(self) -> None:
        symbols = ["S1USDT", "S2USDT", "S3USDT", "S4USDT"]
        market = {
            (sym, interval): _klines(sym, interval, 60 if interval == "15m" else 30)
            for sym in symbols
            for interval in ("15m", "1h")
        }
        contracts = tuple(_contract(s) for s in symbols)
        tickers = tuple(
            Ticker24h(s, Decimal("8000000"), Decimal(str(20 + i)), 1)
            for i, s in enumerate(symbols)
        )
        active = tuple(_active_item(s) for s in symbols)
        client = FakeClient(contracts, tickers, market)
        repo = FakeRepository(active)
        policy = HotlistFunnelPolicy(
            min_move_pct=Decimal("15"),
            min_quote_volume=Decimal("5000000"),
            min_rr=Decimal("2"),
            max_stop_pct=Decimal("5"),
            max_opportunities=3,
        )
        report = HotlistFunnelAnalyzer(client, repo, CONFIG, policy).run()
        step = {s.label: s for s in report.steps}
        self.assertLessEqual(step["final_opportunities"].count, 3)

    def test_top_rejections_at_most_ten(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        self.assertLessEqual(len(report.top_rejections), 10)

    def test_top_rejections_are_rejected_symbol_instances(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        for r in report.top_rejections:
            self.assertIsInstance(r, RejectedSymbol)
            self.assertTrue(r.symbol)
            self.assertTrue(r.reason)

    def test_research_only_flag_is_true(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        self.assertTrue(report.research_only)

    def test_drop_off_pct_is_zero_for_universe_total(self) -> None:
        client, repo = self._make_client_and_repo()
        report = HotlistFunnelAnalyzer(client, repo, CONFIG).run()
        self.assertEqual(0.0, report.steps[0].drop_off_pct)
        self.assertEqual(0, report.steps[0].dropped)


class HotlistFunnelPolicyTest(unittest.TestCase):

    def test_default_policy_is_valid(self) -> None:
        policy = HotlistFunnelPolicy()
        self.assertEqual(Decimal("15"), policy.min_move_pct)
        self.assertEqual(Decimal("5000000"), policy.min_quote_volume)
        self.assertEqual(Decimal("2"), policy.min_rr)
        self.assertEqual(Decimal("5"), policy.max_stop_pct)

    def test_negative_min_move_raises(self) -> None:
        with self.assertRaises(ValueError):
            HotlistFunnelPolicy(min_move_pct=Decimal("-1"))

    def test_rr_below_one_raises(self) -> None:
        with self.assertRaises(ValueError):
            HotlistFunnelPolicy(min_rr=Decimal("0.5"))


class HotlistFunnelReportRenderTest(unittest.TestCase):

    def _make_report(self) -> HotlistFunnelReport:
        steps = [
            FunnelStep("universe_total", 500, 0, 0.0),
            FunnelStep("usdt_perpetual", 400, 100, 20.0),
            FunnelStep("after_exclusions", 380, 20, 5.0),
            FunnelStep("move_ge_min_move", 50, 330, 86.8),
            FunnelStep("volume_ge_min_quote_volume", 30, 20, 40.0),
            FunnelStep("gainers", 18, 12, 40.0),
            FunnelStep("losers", 12, 18, 60.0),
            FunnelStep("watchlist_active", 5, 25, 83.3),
            FunnelStep("review_candidates", 4, 1, 20.0),
            FunnelStep("rr_pass", 3, 1, 25.0),
            FunnelStep("stop_pass", 2, 1, 33.3),
            FunnelStep("final_opportunities", 2, 0, 0.0),
        ]
        rejections = [
            RejectedSymbol("XYZUSDT", "low_move", "change=+3.2% < 15%"),
            RejectedSymbol("ABCUSDT", "low_volume", "vol=1000000 < 5000000"),
        ]
        return HotlistFunnelReport(
            generated_at="2026-06-15T09:00:00+00:00",
            parameters={
                "min_move_pct": "15",
                "min_quote_volume": "5000000",
                "min_rr": "2",
                "max_stop_pct": "5",
            },
            steps=steps,
            top_rejections=rejections,
            final_opportunities=["ALPHAUSDT"],
        )

    def test_report_contains_generated_at(self) -> None:
        md = render_hotlist_funnel(self._make_report())
        self.assertIn("2026-06-15T09:00:00+00:00", md)

    def test_report_contains_all_step_labels(self) -> None:
        md = render_hotlist_funnel(self._make_report())
        for label in (
            "universe_total", "usdt_perpetual", "after_exclusions",
            "move_ge_min_move", "volume_ge_min_quote_volume",
            "gainers", "losers", "watchlist_active",
            "review_candidates", "rr_pass", "stop_pass", "final_opportunities",
        ):
            self.assertIn(label, md)

    def test_report_contains_rejection_symbols(self) -> None:
        md = render_hotlist_funnel(self._make_report())
        self.assertIn("XYZUSDT", md)
        self.assertIn("ABCUSDT", md)

    def test_report_contains_final_opportunity(self) -> None:
        md = render_hotlist_funnel(self._make_report())
        self.assertIn("ALPHAUSDT", md)

    def test_report_contains_research_only_disclaimer(self) -> None:
        md = render_hotlist_funnel(self._make_report())
        self.assertIn("Research only", md)

    def test_report_no_signals_shows_message(self) -> None:
        report = self._make_report()
        report2 = HotlistFunnelReport(
            generated_at=report.generated_at,
            parameters=report.parameters,
            steps=report.steps,
            top_rejections=report.top_rejections,
            final_opportunities=[],
        )
        md = render_hotlist_funnel(report2)
        self.assertIn("No opportunities", md)


class HotlistFunnelCLITest(unittest.TestCase):

    def test_cli_hotlist_funnel_registered(self) -> None:
        args = build_parser().parse_args(["hotlist", "funnel"])
        self.assertEqual("hotlist", args.command)
        self.assertEqual("funnel", args.hotlist_command)

    def test_cli_funnel_default_arguments(self) -> None:
        args = build_parser().parse_args(["hotlist", "funnel"])
        self.assertEqual(Decimal("15"), args.min_move_pct)
        self.assertEqual(Decimal("5000000"), args.min_quote_volume)
        self.assertEqual(Decimal("2"), args.min_rr)
        self.assertEqual(Decimal("5"), args.max_stop_pct)
        self.assertEqual(Path("data/market_data.db"), args.database)
        self.assertEqual(Path("reports/hotlist_funnel.md"), args.report)

    def test_cli_funnel_custom_arguments(self) -> None:
        args = build_parser().parse_args([
            "hotlist", "funnel",
            "--min-move-pct", "20",
            "--min-quote-volume", "10000000",
            "--min-rr", "3",
            "--max-stop-pct", "4",
            "--database", "data/test.db",
            "--report", "reports/test_funnel.md",
        ])
        self.assertEqual(Decimal("20"), args.min_move_pct)
        self.assertEqual(Decimal("10000000"), args.min_quote_volume)
        self.assertEqual(Decimal("3"), args.min_rr)
        self.assertEqual(Decimal("4"), args.max_stop_pct)
        self.assertEqual(Path("data/test.db"), args.database)
        self.assertEqual(Path("reports/test_funnel.md"), args.report)


if __name__ == "__main__":
    unittest.main()
