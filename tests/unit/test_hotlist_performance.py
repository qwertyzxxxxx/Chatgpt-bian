from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.entrypoints.cli import build_parser
from binance_ai_trader.hotlist import (
    HotlistAIReview,
    HotlistPerformanceRepository,
    HotlistPerformanceTracker,
    TrackedHotlistOpportunity,
    evaluate_opportunity,
    format_hotlist_performance_summary,
    render_hotlist_performance,
)


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def review(
    symbol: str = "ALPHAUSDT",
    direction: str = "LONG",
    confidence: str = "STRONG",
) -> HotlistAIReview:
    entry = Decimal("100")
    sign = Decimal("1") if direction == "LONG" else Decimal("-1")
    return HotlistAIReview(
        symbol=symbol,
        direction=direction,
        entry=entry,
        stop_loss=entry - sign * Decimal("5"),
        tp1=entry + sign * Decimal("5"),
        tp2=entry + sign * Decimal("10"),
        rr=Decimal("2"),
        confidence=confidence,
        reason="Research setup.",
        expires_at=(NOW + timedelta(hours=1)).isoformat(timespec="seconds"),
    )


def candle(
    hours: int,
    high: str,
    low: str,
    close: str = "100",
    symbol: str = "ALPHAUSDT",
) -> Kline:
    close_time = NOW + timedelta(hours=hours)
    return Kline(
        symbol=symbol,
        interval="1h",
        open_time_ms=int((close_time - timedelta(hours=1)).timestamp() * 1000),
        close_time_ms=int(close_time.timestamp() * 1000),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        trade_count=100,
    )


class FakeClient:
    def __init__(self, rows: dict[str, tuple[Kline, ...]]) -> None:
        self.rows = rows
        self.calls = []

    def klines(self, symbol: str, interval: str, limit: int = 200):
        self.calls.append((symbol, interval, limit))
        return self.rows.get(symbol, ())


class HotlistPerformanceTrackerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = HotlistPerformanceRepository(
            Path(self.tempdir.name) / "market.db"
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_tracks_required_opportunity_fields_without_duplicates(self) -> None:
        tracker = HotlistPerformanceTracker(FakeClient({}), self.repository)

        first = tracker.track((review(),), NOW)
        second = tracker.track((review(),), NOW)

        self.assertEqual(1, len(self.repository.opportunities()))
        self.assertEqual(first, second)
        tracked = first[0]
        self.assertEqual("ALPHAUSDT", tracked.symbol)
        self.assertEqual("LONG", tracked.direction)
        self.assertEqual(Decimal("95"), tracked.stop_loss)
        self.assertEqual("STRONG", tracked.confidence)
        self.assertEqual(NOW.isoformat(timespec="seconds"), tracked.created_at)

    def test_evaluates_tp2_tp1_sl_expired_and_open(self) -> None:
        base = self.repository.save_opportunity(
            TrackedHotlistOpportunity(
                id=None,
                symbol="ALPHAUSDT",
                direction="LONG",
                entry=Decimal("100"),
                stop_loss=Decimal("95"),
                tp1=Decimal("105"),
                tp2=Decimal("110"),
                rr=Decimal("2"),
                confidence="STRONG",
                created_at=NOW.isoformat(timespec="seconds"),
                expires_at=(NOW + timedelta(hours=1)).isoformat(timespec="seconds"),
            )
        )
        tp2 = evaluate_opportunity(
            base, (candle(1, "111", "99"),), 1, NOW + timedelta(hours=1)
        )
        tp1 = evaluate_opportunity(
            base, (candle(1, "106", "99"),), 1, NOW + timedelta(hours=1)
        )
        stopped = evaluate_opportunity(
            base, (candle(1, "102", "94"),), 1, NOW + timedelta(hours=1)
        )
        expired = evaluate_opportunity(
            base, (candle(1, "102", "98", "101"),), 1, NOW + timedelta(hours=1)
        )
        open_outcome = evaluate_opportunity(
            base, (), 1, NOW + timedelta(hours=1)
        )

        self.assertEqual("TP2_HIT", tp2.status)
        self.assertEqual("TP1_HIT", tp1.status)
        self.assertEqual("SL_HIT", stopped.status)
        self.assertEqual("EXPIRED", expired.status)
        self.assertEqual("OPEN", open_outcome.status)
        self.assertEqual(Decimal("10.0"), tp2.return_pct)
        self.assertEqual(Decimal("-5.00"), stopped.return_pct)

    def test_evaluates_one_four_and_twenty_four_hour_horizons(self) -> None:
        tracker = HotlistPerformanceTracker(
            FakeClient(
                {
                    "ALPHAUSDT": (
                        candle(1, "106", "99"),
                        candle(4, "111", "99"),
                        candle(24, "112", "99"),
                    )
                }
            ),
            self.repository,
        )
        tracker.track((review(),), NOW)

        outcomes = tracker.evaluate(NOW + timedelta(hours=24))

        self.assertEqual([1, 4, 24], [item.horizon_hours for item in outcomes])
        self.assertEqual(
            ["TP1_HIT", "TP2_HIT", "TP2_HIT"],
            [item.status for item in outcomes],
        )

    def test_statistics_report_and_telegram_summary(self) -> None:
        tracker = HotlistPerformanceTracker(
            FakeClient(
                {
                    "ALPHAUSDT": (candle(1, "111", "99"),),
                    "BETAUSDT": (
                        candle(1, "101", "94", symbol="BETAUSDT"),
                    ),
                }
            ),
            self.repository,
        )
        tracker.track(
            (
                review("ALPHAUSDT", confidence="STRONG"),
                review("BETAUSDT", confidence="WEAK"),
            ),
            NOW,
        )
        tracker.evaluate(NOW + timedelta(hours=1))

        statistics = tracker.statistics()
        report = render_hotlist_performance(
            statistics,
            self.repository.opportunities(limit=50),
            self.repository.outcomes(),
            (NOW + timedelta(hours=1)).isoformat(timespec="seconds"),
        )
        message = format_hotlist_performance_summary(statistics)

        self.assertEqual(2, statistics.total_opportunities)
        self.assertEqual(Decimal("50.0"), statistics.win_rate)
        self.assertEqual(Decimal("50.0"), statistics.tp2_rate)
        self.assertEqual(Decimal("2"), statistics.average_rr)
        for expected in (
            "Performance by Confidence",
            "Best Symbols",
            "Worst Symbols",
            "Last 50 Opportunities",
            "STRONG",
            "WEAK",
            "Research only",
        ):
            self.assertIn(expected, report)
        self.assertIn("胜率: 50.00%", message)
        self.assertIn("仅供研究", message)

    def test_cli_defaults_and_public_data_only_configuration(self) -> None:
        args = build_parser().parse_args(["hotlist-performance"])

        self.assertEqual("hotlist-performance", args.command)
        self.assertEqual(Path("data/market_data.db"), args.database)
        self.assertEqual(Path("reports/hotlist_performance.md"), args.report)
        self.assertEqual("https://fapi.binance.com", args.base_url)


if __name__ == "__main__":
    unittest.main()
