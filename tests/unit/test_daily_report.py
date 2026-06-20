from datetime import date
from types import SimpleNamespace
import unittest

from binance_ai_trader.reporting import DailyReportService, format_top3_message


class DailyReportTest(unittest.TestCase):
    def test_report_includes_top3_without_changing_ranking(self) -> None:
        top3 = [
            {"symbol": "BTCUSDT", "direction": "LONG", "score": 91,
             "entry": "100", "sl": "95", "tp1": "105", "tp2": "110", "rr": "2"}
        ]
        repository = SimpleNamespace(
            load_or_create_paper_account=lambda *_args, **_kwargs: SimpleNamespace(
                equity=1000, mode="AGGRESSIVE", consecutive_losses=0,
                paused_until=None, current_target=10000, aggressive_allowed=True,
            ),
            load_signal_report=lambda *_args: [],
            load_daily_top_signals=lambda *_args: top3,
            load_regime_report=lambda *_args: None,
            load_sector_report=lambda *_args: [],
            load_top_capital_signals=lambda *_args: [],
            load_top_candidate_report=lambda *_args: [],
        )

        report = DailyReportService(repository).build(date(2026, 6, 11))

        self.assertIs(top3, report["top3"])
        message = format_top3_message(report)
        self.assertIn("Top3 — 2026-06-11", message)
        self.assertIn("BTCUSDT", message)
        self.assertIn("LONG", message)
        self.assertIn("SL 95", message)


if __name__ == "__main__":
    unittest.main()
