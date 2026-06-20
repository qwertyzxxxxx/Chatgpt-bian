from datetime import UTC, datetime
from decimal import Decimal
import unittest

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.domain.models import Contract, Kline, Ticker24h
from binance_ai_trader.entrypoints.cli import build_parser
from binance_ai_trader.hotlist import (
    HotlistWatcher,
    HotlistWatcherPolicy,
    format_hotlist_message,
)


def contract(symbol: str, base: str | None = None, status: str = "TRADING") -> Contract:
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


def candles(symbol: str, interval: str, start: str, step: str, count: int = 60) -> tuple[Kline, ...]:
    price = Decimal(start)
    increment = Decimal(step)
    duration = 900_000 if interval == "15m" else 3_600_000
    rows = []
    for index in range(count):
        close = price + increment * index
        rows.append(
            Kline(
                symbol=symbol,
                interval=interval,
                open_time_ms=index * duration,
                close_time_ms=(index + 1) * duration - 1,
                open=close - increment / 2,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("100"),
                quote_volume=Decimal("1000000") + index * Decimal("10000"),
                trade_count=100,
            )
        )
    return tuple(rows)


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
        return self._market[(symbol, interval)][-limit:]


CONFIG = UniverseConfig(
    minimum_quote_volume_24h=Decimal("5000000"),
    stablecoin_base_assets=frozenset({"USDC", "BUSD"}),
    leveraged_token_suffixes=("UP", "DOWN", "BULL", "BEAR"),
    denied_symbols=frozenset({"DENIEDUSDT"}),
)


class HotlistWatcherTest(unittest.TestCase):
    def test_cli_supports_watch_and_scan_aliases_with_required_options(self) -> None:
        for command in ("watch", "scan"):
            args = build_parser().parse_args([
                "hotlist",
                command,
                "--limit",
                "3",
                "--min-move-pct",
                "20",
                "--min-quote-volume",
                "10000000",
                "--expiry-minutes",
                "30",
                "--database",
                "data/test.db",
            ])
            self.assertEqual(command, args.hotlist_command)
            self.assertEqual(3, args.limit)
            self.assertEqual(Decimal("20"), args.min_move_pct)
            self.assertEqual(Decimal("10000000"), args.min_quote_volume)
            self.assertEqual(30, args.expiry_minutes)

    def test_candidate_filtering_and_stablecoin_exclusion(self) -> None:
        client = FakeClient(
            (
                contract("ALPHAUSDT"),
                contract("BETAUSDT"),
                contract("USDCUSDT", "USDC"),
                contract("DENIEDUSDT"),
                contract("OLDUSDT", status="BREAK"),
            ),
            (
                Ticker24h("ALPHAUSDT", Decimal("6000000"), Decimal("16"), 1),
                Ticker24h("BETAUSDT", Decimal("4000000"), Decimal("-20"), 1),
                Ticker24h("USDCUSDT", Decimal("9000000"), Decimal("30"), 1),
                Ticker24h("DENIEDUSDT", Decimal("9000000"), Decimal("30"), 1),
                Ticker24h("OLDUSDT", Decimal("9000000"), Decimal("30"), 1),
            ),
        )

        candidates = HotlistWatcher(client, CONFIG).candidates()

        self.assertEqual(["ALPHAUSDT"], [item.symbol for item in candidates])
        self.assertEqual("LONG", candidates[0].direction)

    def test_long_entry_plan_waits_for_pullback_and_has_two_r_target(self) -> None:
        plan = self._single_plan("LONGUSDT", Decimal("20"), "100", "1")

        self.assertEqual("LONG", plan.direction)
        self.assertLess(plan.suggested_limit_entry, plan.current_price)
        self.assertLess(plan.stop_loss, plan.suggested_limit_entry)
        risk = plan.suggested_limit_entry - plan.stop_loss
        self.assertEqual(risk, plan.tp1 - plan.suggested_limit_entry)
        self.assertEqual(risk * 2, plan.tp2 - plan.suggested_limit_entry)
        self.assertEqual(Decimal("2.00"), plan.rr)
        self.assertIn("EMA20", plan.reason)

    def test_short_entry_plan_waits_for_retest_and_has_two_r_target(self) -> None:
        plan = self._single_plan("SHORTUSDT", Decimal("-20"), "200", "-1")

        self.assertEqual("SHORT", plan.direction)
        self.assertGreater(plan.suggested_limit_entry, plan.current_price)
        self.assertGreater(plan.stop_loss, plan.suggested_limit_entry)
        risk = plan.stop_loss - plan.suggested_limit_entry
        self.assertEqual(risk, plan.suggested_limit_entry - plan.tp1)
        self.assertEqual(risk * 2, plan.suggested_limit_entry - plan.tp2)
        self.assertIn("EMA20", plan.reason)

    def test_expiry_and_top_five_cap(self) -> None:
        symbols = tuple(f"S{index}USDT" for index in range(7))
        market = {
            (symbol, interval): candles(symbol, interval, "100", "1", 60 if interval == "15m" else 30)
            for symbol in symbols
            for interval in ("15m", "1h")
        }
        watcher = HotlistWatcher(
            FakeClient(
                tuple(contract(symbol) for symbol in symbols),
                tuple(
                    Ticker24h(symbol, Decimal("10000000"), Decimal(str(30 - index)), 1)
                    for index, symbol in enumerate(symbols)
                ),
                market,
            ),
            CONFIG,
        )

        plans = watcher.watch(datetime(2026, 6, 14, 12, 0, tzinfo=UTC))

        self.assertEqual(5, len(plans))
        self.assertTrue(all(item.expires_at == "2026-06-14T13:00:00+00:00" for item in plans))
        self.assertIn("仅供研究", format_hotlist_message(plans))

    @staticmethod
    def _single_plan(
        symbol: str, change: Decimal, start: str, step: str
    ):
        market = {
            (symbol, "15m"): candles(symbol, "15m", start, step),
            (symbol, "1h"): candles(symbol, "1h", start, step, 30),
        }
        watcher = HotlistWatcher(
            FakeClient(
                (contract(symbol),),
                (Ticker24h(symbol, Decimal("10000000"), change, 1),),
                market,
            ),
            CONFIG,
        )
        return watcher.watch(datetime(2026, 6, 14, 12, 0, tzinfo=UTC))[0]


if __name__ == "__main__":
    unittest.main()
