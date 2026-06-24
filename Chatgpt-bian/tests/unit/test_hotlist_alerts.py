from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import sqlite3
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
    SkippedAlert,
    alert_level,
    format_hotlist_alert_batch_message,
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

    def test_cooldown_blocks_same_symbol_within_window(self) -> None:
        opportunity = plan()
        self._watch(opportunity.symbol)
        engine = HotlistAlertEngine(
            FakeReview((opportunity,)), self.repository, cooldown_hours=1
        )

        first, _, _ = engine.generate(NOW)
        duplicate, _, _ = engine.generate(NOW + timedelta(minutes=59))
        after_window, _, _ = engine.generate(NOW + timedelta(minutes=61))

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

        alerts, _, _ = HotlistAlertEngine(
            FakeReview(opportunities), self.repository
        ).generate(NOW)

        self.assertEqual(["GOODUSDT"], [item.symbol for item in alerts])

    def test_alert_levels_and_telegram_message(self) -> None:
        self.assertEqual("HIGH", alert_level(Decimal("3")))
        self.assertEqual("MEDIUM", alert_level(Decimal("2")))
        self.assertEqual("LOW", alert_level(Decimal("1.99")))
        opportunity = plan(rr="3")
        self._watch(opportunity.symbol)
        alerts, _, _ = HotlistAlertEngine(
            FakeReview((opportunity,)), self.repository
        ).generate(NOW)

        message = format_hotlist_alert_message(alerts[0])

        for expected in (
            "🔔 Hotlist 警报",
            opportunity.symbol,
            opportunity.direction,
            "买入:",
            "止损:",
            "TP1:",
            "TP2:",
            "RR:",
            "到期:",
            "理由:",
            "仅供研究",
        ):
            self.assertIn(expected, message)

    def test_daily_summary_and_command_scaffold(self) -> None:
        good = plan("GOODUSDT")
        expired = plan("OLDUSDT")
        self._watch(good.symbol)
        self._watch(expired.symbol, status="EXPIRED")
        _, _, summary = HotlistAlertEngine(
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
        self.assertEqual(4, args.hotlist_alert_cooldown_hours)
        disabled = build_parser().parse_args(["run-loop"])
        enabled = build_parser().parse_args([
            "run-loop", "--enable-hotlist-alerts"
        ])
        self.assertFalse(disabled.enable_hotlist_alerts)
        self.assertTrue(enabled.enable_hotlist_alerts)
        self.assertEqual(4, disabled.hotlist_alert_cooldown_hours)

    def test_runner_task_skips_without_telegram_and_sends_batch(self) -> None:
        args = SimpleNamespace(
            base_url="https://fapi.binance.com",
            timeout=1.0,
            max_retries=0,
            database=Path(self.tempdir.name) / "runner.db",
            config=Path("config/universe.json"),
            hotlist_alert_cooldown_hours=4,
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
            engine.return_value.generate.return_value = ((alert,), (), summary)
            report = Path(self.tempdir.name) / "summary.md"
            path_type.return_value = report
            result = _run_hotlist_alert_task(args, notifier)
        self.assertEqual(1, result.details["alerts_generated"])
        self.assertEqual(1, result.details["alerts_sent"])
        self.assertEqual(1, len(notifier.messages))
        self.assertIn("仅供研究", notifier.messages[0])
        self.assertIn("🔥 Hotlist Alert", notifier.messages[0])
        self.assertNotIn("Top1", notifier.messages[0])

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
            engine.return_value.generate.return_value = ((), (), summary)
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

    def _insert_opportunity(
        self,
        symbol: str,
        direction: str = "LONG",
        expiry: str | None = None,
    ) -> None:
        """Insert a row directly into hotlist_opportunities via the repo connection."""
        if expiry is None:
            expiry = (NOW + timedelta(hours=1)).isoformat(timespec="seconds")
        self.repository._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hotlist_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry TEXT NOT NULL DEFAULT '100',
                sl TEXT NOT NULL DEFAULT '95',
                tp1 TEXT NOT NULL DEFAULT '105',
                tp2 TEXT NOT NULL DEFAULT '110',
                rr TEXT NOT NULL DEFAULT '2',
                confidence TEXT NOT NULL DEFAULT 'MEDIUM',
                created_at TEXT NOT NULL,
                expiry TEXT NOT NULL
            )
            """
        )
        self.repository._connection.execute(
            """
            INSERT INTO hotlist_opportunities
            (symbol, direction, entry, sl, tp1, tp2, rr, confidence, created_at, expiry)
            VALUES (?, ?, '100', '95', '105', '110', '2', 'MEDIUM', ?, ?)
            """,
            (symbol, direction, NOW.isoformat(timespec="seconds"), expiry),
        )
        self.repository._connection.commit()


class DeduplicationRulesTest(unittest.TestCase):
    """Tests for Rules 1-6: open-position check, cooldown, direction conflict, batch."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = HotlistWatchlistRepository(
            Path(self.tempdir.name) / "market.db"
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def _watch(self, symbol: str, status: str = "ACTIVE") -> None:
        self.repository.save(
            HotlistWatchlistItem(
                symbol=symbol,
                source="GAINER",
                first_seen_at=NOW.isoformat(timespec="seconds"),
                last_seen_at=NOW.isoformat(timespec="seconds"),
                expires_at=(NOW + timedelta(hours=2)).isoformat(timespec="seconds"),
                observation_count=1,
                last_rank=1,
                status=status,
            )
        )

    def _insert_opportunity(
        self,
        symbol: str,
        direction: str = "LONG",
        expiry: str | None = None,
    ) -> None:
        if expiry is None:
            expiry = (NOW + timedelta(hours=1)).isoformat(timespec="seconds")
        self.repository._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hotlist_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry TEXT NOT NULL DEFAULT '100',
                sl TEXT NOT NULL DEFAULT '95',
                tp1 TEXT NOT NULL DEFAULT '105',
                tp2 TEXT NOT NULL DEFAULT '110',
                rr TEXT NOT NULL DEFAULT '2',
                confidence TEXT NOT NULL DEFAULT 'MEDIUM',
                created_at TEXT NOT NULL,
                expiry TEXT NOT NULL
            )
            """
        )
        self.repository._connection.execute(
            """
            INSERT INTO hotlist_opportunities
            (symbol, direction, entry, sl, tp1, tp2, rr, confidence, created_at, expiry)
            VALUES (?, ?, '100', '95', '105', '110', '2', 'MEDIUM', ?, ?)
            """,
            (symbol, direction, NOW.isoformat(timespec="seconds"), expiry),
        )
        self.repository._connection.commit()

    def test_rule1_open_opportunity_blocks_same_symbol(self) -> None:
        """Rule 1: symbol with non-expired hotlist_opportunities → skip."""
        p = plan("EDENUSDT")
        self._watch("EDENUSDT")
        self._insert_opportunity("EDENUSDT", direction="LONG")

        alerts, skipped, _ = HotlistAlertEngine(
            FakeReview((p,)), self.repository
        ).generate(NOW)

        self.assertEqual(0, len(alerts))
        self.assertEqual(1, len(skipped))
        self.assertIn(
            skipped[0].reason, ("duplicate_open_symbol", "opposite_direction_open")
        )
        self.assertEqual("EDENUSDT", skipped[0].symbol)

    def test_rule2_cooldown_blocks_within_4_hours(self) -> None:
        """Rule 2: same symbol pushed < 4h ago → cooldown_active."""
        p = plan("BRUSDT")
        self._watch("BRUSDT")
        engine = HotlistAlertEngine(
            FakeReview((p,)), self.repository, cooldown_hours=4
        )
        first, _, _ = engine.generate(NOW)
        self.assertEqual(1, len(first))

        blocked, skipped, _ = engine.generate(NOW + timedelta(hours=3))
        self.assertEqual(0, len(blocked))
        self.assertEqual(1, len(skipped))
        self.assertEqual("cooldown_active", skipped[0].reason)

    def test_rule3_cooldown_expires_after_n_hours(self) -> None:
        """Rule 3: after cooldown window passes, alert is allowed again."""
        p = plan("BRUSDT")
        self._watch("BRUSDT")
        engine = HotlistAlertEngine(
            FakeReview((p,)), self.repository, cooldown_hours=4
        )
        engine.generate(NOW)

        allowed, skipped, _ = engine.generate(NOW + timedelta(hours=5))
        self.assertEqual(1, len(allowed))
        self.assertEqual(0, len(skipped))

    def test_rule4_long_open_blocks_short(self) -> None:
        """Rule 4: OPEN LONG present → SHORT for same symbol → opposite_direction_open."""
        long_p = plan("XUSDT", entry="100", stop="95")
        self.assertEqual("LONG", long_p.direction)
        short_p = plan("XUSDT", entry="100", stop="105")
        self.assertEqual("SHORT", short_p.direction)

        self._watch("XUSDT")
        self._insert_opportunity("XUSDT", direction="LONG")

        alerts, skipped, _ = HotlistAlertEngine(
            FakeReview((short_p,)), self.repository
        ).generate(NOW)

        self.assertEqual(0, len(alerts))
        self.assertEqual(1, len(skipped))
        self.assertEqual("opposite_direction_open", skipped[0].reason)

    def test_rule5_hotlist_alerts_history_deduplicates_without_opportunities(
        self,
    ) -> None:
        """Rule 5: hotlist_alerts has history; hotlist_opportunities empty → still dedup."""
        p = plan("ZXUSDT")
        self._watch("ZXUSDT")
        engine = HotlistAlertEngine(
            FakeReview((p,)), self.repository, cooldown_hours=4
        )
        first, _, _ = engine.generate(NOW)
        self.assertEqual(1, len(first))
        self.assertEqual(0, self.repository.has_open_opportunity("ZXUSDT", NOW.isoformat(timespec="seconds")))

        blocked, skipped, _ = engine.generate(NOW + timedelta(hours=1))
        self.assertEqual(0, len(blocked))
        self.assertEqual("cooldown_active", skipped[0].reason)

    def test_rule6_top3_merged_telegram(self) -> None:
        """Rule 6: up to 3 alerts merged into one batch Telegram message."""
        symbols = ["AAUSDT", "BBUSDT", "CCUSDT"]
        plans = tuple(plan(s) for s in symbols)
        for s in symbols:
            self._watch(s)

        alerts, _, _ = HotlistAlertEngine(
            FakeReview(plans), self.repository
        ).generate(NOW)
        self.assertEqual(3, len(alerts))

        msg = format_hotlist_alert_batch_message(alerts)
        self.assertIn("🔥 Hotlist Alert", msg)
        self.assertNotIn("Top3", msg)
        self.assertIn("AAUSDT", msg)
        self.assertIn("BBUSDT", msg)
        self.assertIn("CCUSDT", msg)
        self.assertIn("LONG", msg)
        self.assertIn("买入:", msg)
        self.assertIn("止损:", msg)
        self.assertIn("TP1:", msg)
        self.assertIn("TP2:", msg)
        self.assertIn("RR:", msg)
        self.assertIn("仅供研究", msg)
        self.assertIn("24h涨跌:", msg)

    def test_rule7_skip_reason_recorded(self) -> None:
        """Rule 7: skip reasons correctly recorded in skipped tuple."""
        bad_rr = plan("BADRR", rr="1.5")
        self._watch("BADRR")
        cooldown_sym = plan("COOL")
        self._watch("COOL")
        open_sym = plan("OPENSYM")
        self._watch("OPENSYM")
        self._insert_opportunity("OPENSYM", direction="LONG")

        engine = HotlistAlertEngine(
            FakeReview((bad_rr, cooldown_sym, open_sym)),
            self.repository,
            cooldown_hours=4,
        )
        first_alerts, first_skipped, _ = engine.generate(NOW)
        self.assertEqual(1, len(first_alerts))
        self.assertEqual("COOL", first_alerts[0].symbol)

        reasons = {s.symbol: s.reason for s in first_skipped}
        self.assertEqual("missing_plan", reasons["BADRR"])
        self.assertIn("OPENSYM", reasons)
        self.assertIn(
            reasons["OPENSYM"], ("duplicate_open_symbol", "opposite_direction_open")
        )

        _, second_skipped, _ = engine.generate(NOW + timedelta(hours=1))
        second_reasons = {s.symbol: s.reason for s in second_skipped}
        self.assertEqual("cooldown_active", second_reasons.get("COOL"))

    def test_rule8_single_alert_sends_one_batch_message(self) -> None:
        """Rule 8: even a single alert goes through batch format."""
        p = plan("SOLUSDT")
        msg = format_hotlist_alert_batch_message((
            HotlistAlert(
                symbol=p.symbol,
                direction=p.direction,
                entry=p.suggested_limit_entry,
                created_at=NOW.isoformat(timespec="seconds"),
                level="MEDIUM",
                plan=p,
            ),
        ))
        self.assertIn("🔥 Hotlist Alert", msg)
        self.assertNotIn("Top1", msg)
        self.assertIn("SOLUSDT", msg)
        self.assertIn("仅供研究", msg)
        self.assertNotIn("2. ", msg)

    def test_batch_message_empty_returns_empty_string(self) -> None:
        """Edge: empty alert list → empty string (no message sent)."""
        msg = format_hotlist_alert_batch_message(())
        self.assertEqual("", msg)


if __name__ == "__main__":
    unittest.main()
