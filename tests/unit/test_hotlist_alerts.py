from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from binance_ai_trader.entrypoints.cli import _run_hotlist_alert_task, build_parser
from binance_ai_trader.hotlist import (
    HotlistAlert,
    HotlistAlertEngine,
    HotlistDailySummary,
    HotlistWatchlistItem,
    HotlistWatchlistRepository,
    alert_level,
    format_hotlist_alert_message,
    render_hotlist_daily_summary,
)
from binance_ai_trader.hotlist.models import HotlistEntryPlan


NOW = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)


def plan(
    symbol: str = "ALPHAUSDT",
    rr: str = "2",
    entry: str = "100",
    stop: str = "95",
    volume: str = "10000000",
) -> HotlistEntryPlan:
    entry_value = Decimal(entry)
    stop_value = Decimal(stop)
    risk = abs(entry_value - stop_value)
    direction = "LONG" if stop_value < entry_value else "SHORT"
    sign = Decimal("1") if direction == "LONG" else Decimal("-1")
    return HotlistEntryPlan(
        symbol=symbol,
        direction=direction,
        current_price=entry_value,
        change_24h_pct=Decimal("20"),
        quote_volume=Decimal(volume),
        volume_ratio_15m=Decimal("2"),
        ema20_15m=entry_value,
        atr14=Decimal("2"),
        swing_high=entry_value + Decimal("5"),
        swing_low=entry_value - Decimal("5"),
        suggested_limit_entry=entry_value,
        stop_loss=stop_value,
        tp1=entry_value + sign * risk,
        tp2=entry_value + sign * risk * Decimal(rr),
        rr=Decimal(rr),
        expires_at=(NOW + timedelta(minutes=60)).isoformat(timespec="seconds"),
        reason="Momentum retest with public market data.",
    )


class FakeReview:
    def __init__(self, plans: tuple[HotlistEntryPlan, ...]) -> None:
        self.plans = plans

    def review(self, now=None) -> tuple[HotlistEntryPlan, ...]:
        return self.plans


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


class HotlistAlertEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = HotlistWatchlistRepository(
            Path(self.tempdir.name) / "market.db"
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_deduplicates_same_symbol_and_direction_for_sixty_minutes(self) -> None:
        opportunity = plan()
        self._watch(opportunity.symbol)
        engine = HotlistAlertEngine(FakeReview((opportunity,)), self.repository)

        first, _ = engine.generate(NOW)
        duplicate, _ = engine.generate(NOW + timedelta(minutes=59))
        after_window, _ = engine.generate(NOW + timedelta(minutes=60))

        self.assertEqual(1, len(first))
        self.assertEqual(0, len(duplicate))
        self.assertEqual(1, len(after_window))
        self.assertEqual(2, self.repository.alert_count())

    def test_quality_filter_requires_rr_stop_volume_and_active_status(self) -> None:
        opportunities = (
            plan("GOODUSDT"),
            plan("RRUSDT", rr="1.9"),
            plan("STOPUSDT", stop="94"),
            plan("VOLUMEUSDT", volume="4999999"),
            plan("EXPIREDUSDT"),
        )
        for item in opportunities:
            self._watch(
                item.symbol,
                status="EXPIRED" if item.symbol == "EXPIREDUSDT" else "ACTIVE",
            )

        alerts, _ = HotlistAlertEngine(
            FakeReview(opportunities), self.repository
        ).generate(NOW)

        self.assertEqual(["GOODUSDT"], [item.symbol for item in alerts])

    def test_alert_levels_and_telegram_message(self) -> None:
        self.assertEqual("HIGH", alert_level(Decimal("3")))
        self.assertEqual("MEDIUM", alert_level(Decimal("2")))
        self.assertEqual("LOW", alert_level(Decimal("1.99")))
        opportunity = plan(rr="3")
        self._watch(opportunity.symbol)
        alerts, _ = HotlistAlertEngine(
            FakeReview((opportunity,)), self.repository
        ).generate(NOW)

        message = format_hotlist_alert_message(alerts[0])

        for expected in (
            "HOTLIST ALERT",
            opportunity.symbol,
            opportunity.direction,
            "entry:",
            "SL:",
            "TP1:",
            "TP2:",
            "RR:",
            "expiry:",
            "reason:",
            "Research only",
        ):
            self.assertIn(expected, message)

    def test_daily_summary_and_command_scaffold(self) -> None:
        good = plan("GOODUSDT")
        expired = plan("OLDUSDT")
        self._watch(good.symbol)
        self._watch(expired.symbol, status="EXPIRED")
        _, summary = HotlistAlertEngine(
            FakeReview((good,)), self.repository
        ).generate(NOW)

        report = render_hotlist_daily_summary(summary)

        self.assertIn("**Symbols watched:** 2", report)
        self.assertIn("**Alerts generated:** 1", report)
        self.assertIn("**Expired symbols:** 1", report)
        self.assertIn("## Top Opportunities", report)
        self.assertIn("`GOODUSDT`", report)
        args = build_parser().parse_args([
            "hotlist-alert",
            "--database",
            "data/market_data.db",
        ])
        self.assertEqual("hotlist-alert", args.command)
        disabled = build_parser().parse_args(["run-loop"])
        enabled = build_parser().parse_args([
            "run-loop", "--enable-hotlist-alerts"
        ])
        self.assertFalse(disabled.enable_hotlist_alerts)
        self.assertTrue(enabled.enable_hotlist_alerts)

    def test_runner_task_skips_without_telegram_and_sends_only_existing_alerts(self) -> None:
        args = SimpleNamespace(
            base_url="https://fapi.binance.com",
            timeout=1.0,
            max_retries=0,
            database=Path(self.tempdir.name) / "runner.db",
            config=Path("config/universe.json"),
        )
        skipped = _run_hotlist_alert_task(args, None)
        self.assertEqual("SKIPPED", skipped.status)
        self.assertEqual(
            "telegram_not_configured", skipped.details["skipped_reason"]
        )

        opportunity = plan()
        alert = HotlistAlert(
            symbol=opportunity.symbol,
            direction=opportunity.direction,
            entry=opportunity.suggested_limit_entry,
            created_at=NOW.isoformat(timespec="seconds"),
            level="MEDIUM",
            plan=opportunity,
        )
        summary = HotlistDailySummary(
            generated_at=NOW.isoformat(timespec="seconds"),
            symbols_watched=1,
            alerts_generated=1,
            expired_symbols=0,
            top_opportunities=(opportunity,),
        )
        notifier = FakeNotifier()
        with (
            patch("binance_ai_trader.entrypoints.cli.BinancePublicClient"),
            patch("binance_ai_trader.entrypoints.cli.HotlistWatchlistRepository"),
            patch("binance_ai_trader.entrypoints.cli.UniverseConfig.load"),
            patch("binance_ai_trader.entrypoints.cli.HotlistWatchlist"),
            patch(
                "binance_ai_trader.entrypoints.cli.HotlistAlertEngine"
            ) as engine,
            patch("binance_ai_trader.entrypoints.cli.Path") as path_type,
        ):
            engine.return_value.generate.return_value = ((alert,), summary)
            report = Path(self.tempdir.name) / "summary.md"
            path_type.return_value = report
            result = _run_hotlist_alert_task(args, notifier)
        self.assertEqual(1, result.details["alerts_generated"])
        self.assertEqual(1, result.details["alerts_sent"])
        self.assertEqual(1, len(notifier.messages))
        self.assertIn("Research only", notifier.messages[0])

        notifier = FakeNotifier()
        with (
            patch("binance_ai_trader.entrypoints.cli.BinancePublicClient"),
            patch("binance_ai_trader.entrypoints.cli.HotlistWatchlistRepository"),
            patch("binance_ai_trader.entrypoints.cli.UniverseConfig.load"),
            patch("binance_ai_trader.entrypoints.cli.HotlistWatchlist"),
            patch(
                "binance_ai_trader.entrypoints.cli.HotlistAlertEngine"
            ) as engine,
            patch("binance_ai_trader.entrypoints.cli.Path") as path_type,
        ):
            engine.return_value.generate.return_value = ((), summary)
            path_type.return_value = Path(self.tempdir.name) / "empty.md"
            result = _run_hotlist_alert_task(args, notifier)
        self.assertEqual(0, result.details["alerts_sent"])
        self.assertEqual([], notifier.messages)

    def _watch(self, symbol: str, status: str = "ACTIVE") -> None:
        self.repository.save(
            HotlistWatchlistItem(
                symbol=symbol,
                source="GAINER",
                first_seen_at=NOW.isoformat(timespec="seconds"),
                last_seen_at=NOW.isoformat(timespec="seconds"),
                expires_at=(NOW + timedelta(minutes=60)).isoformat(timespec="seconds"),
                observation_count=1,
                last_rank=1,
                status=status,
            )
        )


if __name__ == "__main__":
    unittest.main()
