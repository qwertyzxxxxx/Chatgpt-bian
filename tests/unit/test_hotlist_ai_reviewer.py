from datetime import UTC, datetime, timedelta
from decimal import Decimal
import unittest

from binance_ai_trader.entrypoints.cli import build_parser
from binance_ai_trader.hotlist import (
    format_hotlist_ai_review_message,
    render_hotlist_top5_review,
    review_hotlist_opportunities,
)
from binance_ai_trader.hotlist.models import HotlistEntryPlan


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def plan(
    symbol: str,
    rr: str,
    volume_ratio: str,
    move: str = "20",
    direction: str = "LONG",
) -> HotlistEntryPlan:
    entry = Decimal("100")
    risk = Decimal("4")
    sign = Decimal("1") if direction == "LONG" else Decimal("-1")
    return HotlistEntryPlan(
        symbol=symbol,
        direction=direction,
        current_price=entry,
        change_24h_pct=Decimal(move),
        quote_volume=Decimal("10000000"),
        volume_ratio_15m=Decimal(volume_ratio),
        ema20_15m=entry,
        atr14=Decimal("2"),
        swing_high=Decimal("105"),
        swing_low=Decimal("95"),
        suggested_limit_entry=entry,
        stop_loss=entry - sign * risk,
        tp1=entry + sign * risk,
        tp2=entry + sign * risk * Decimal(rr),
        rr=Decimal(rr),
        expires_at=(NOW + timedelta(minutes=60)).isoformat(timespec="seconds"),
        reason="Public-data momentum retest.",
    )


class HotlistAIReviewerTest(unittest.TestCase):
    def test_scores_confidence_and_returns_top_five(self) -> None:
        plans = (
            plan("STRONGUSDT", "3", "1.5"),
            plan("MEDIUMUSDT", "2", "1"),
            plan("WEAKUSDT", "1.5", "0.8"),
            plan("FOURUSDT", "2.5", "1.2"),
            plan("FIVEUSDT", "2.2", "1.1", direction="SHORT"),
            plan("SIXUSDT", "1", "0.5"),
        )

        reviews = review_hotlist_opportunities(plans)

        self.assertEqual(5, len(reviews))
        self.assertEqual("STRONGUSDT", reviews[0].symbol)
        self.assertEqual("STRONG", reviews[0].confidence)
        self.assertEqual("MEDIUM", reviews[1].confidence)
        self.assertEqual("WEAK", reviews[-1].confidence)
        self.assertEqual("SHORT", next(
            item.direction for item in reviews if item.symbol == "FIVEUSDT"
        ))

    def test_output_contract_report_and_telegram_formatter(self) -> None:
        reviews = review_hotlist_opportunities(
            (plan("ALPHAUSDT", "3", "2"),)
        )

        report = render_hotlist_top5_review(
            reviews, NOW.isoformat(timespec="seconds")
        )
        message = format_hotlist_ai_review_message(reviews)

        for expected in (
            "ALPHAUSDT",
            "LONG",
            "100",
            "96",
            "104",
            "112",
            "3",
            "STRONG",
            "STRONG research setup",
            reviews[0].expires_at,
        ):
            self.assertIn(expected, report)
            self.assertIn(expected, message)
        self.assertIn("Research only", report)
        self.assertIn("仅供研究", message)

    def test_command_defaults_to_top_five_markdown_report(self) -> None:
        args = build_parser().parse_args(["hotlist-ai-review"])

        self.assertEqual("hotlist-ai-review", args.command)
        self.assertEqual(5, args.limit)
        self.assertEqual("reports/hotlist_top5_review.md", str(args.report))
        self.assertEqual("https://fapi.binance.com", args.base_url)

    def test_rejects_limits_above_top_five(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 5"):
            review_hotlist_opportunities((), limit=6)


if __name__ == "__main__":
    unittest.main()
