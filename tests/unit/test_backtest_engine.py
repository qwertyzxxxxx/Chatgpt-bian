from decimal import Decimal
import unittest

from binance_ai_trader.backtest import BacktestPolicy, summarize_results
from binance_ai_trader.domain.models import BacktestResult


def result(
    symbol: str,
    outcome: str,
    realized_r: str,
    regime: str = "BULL",
    sector: str = "LAYER1",
    score: float = 92,
    time_ms: int = 1,
    direction: str = "LONG",
) -> BacktestResult:
    return BacktestResult(
        evaluation_time_ms=time_ms,
        symbol=symbol,
        direction=direction,
        combined_regime=regime,
        sector=sector,
        sector_rank=1,
        score=score,
        entry=Decimal("100"),
        stop_loss=Decimal("98"),
        tp1=Decimal("102.4"),
        tp2=Decimal("105"),
        rr_tp1=Decimal("1.2"),
        rr_tp2=Decimal("2.5"),
        result=outcome,
        bars_to_result=2,
        realized_r=Decimal(realized_r),
    )


class BacktestMetricsTest(unittest.TestCase):
    def test_calculates_required_metrics_and_groups(self) -> None:
        results = (
            result("LOSSUSDT", "LOSS", "-1", score=65, time_ms=1),
            result("TP1USDT", "TP1_HIT", "1.2", regime="RANGE", sector="DEFI", score=75, time_ms=2),
            result("TP2USDT", "WIN_TP2", "2.5", score=85, time_ms=3, direction="SHORT", regime="BEAR"),
            result("EXPIREDUSDT", "EXPIRED", "0", sector="MEME", score=95, time_ms=4),
        )
        summary = summarize_results("run", "start", "end", 10, results)

        self.assertEqual(4, summary.metrics.total_signals)
        self.assertEqual(50.0, summary.metrics.tp1_hit_rate)
        self.assertEqual(25.0, summary.metrics.tp2_win_rate)
        self.assertEqual(25.0, summary.metrics.loss_rate)
        self.assertEqual(25.0, summary.metrics.expired_rate)
        self.assertEqual(3.7, summary.metrics.profit_factor)
        self.assertEqual(0.675, summary.metrics.expectancy_r)
        self.assertEqual(1.0, summary.metrics.max_drawdown_r)
        self.assertEqual(2.5, summary.metrics.avg_rr_tp2)
        self.assertEqual({"LONG", "SHORT"}, set(summary.by_direction))
        self.assertEqual(3, summary.by_direction["LONG"].total_signals)
        self.assertEqual(1, summary.by_direction["SHORT"].total_signals)
        self.assertEqual({"BULL", "BEAR", "RANGE", "OBSERVE"}, set(summary.by_combined_regime))
        self.assertEqual({"90-100", "80-90", "70-80", "below 70"}, set(summary.by_score_bucket))

    def test_profit_factor_is_null_without_losses(self) -> None:
        summary = summarize_results("run", "start", "end", 1, (result("WINUSDT", "WIN_TP2", "2.5"),))
        self.assertIsNone(summary.metrics.profit_factor)

    def test_policy_supports_configurable_positive_horizon(self) -> None:
        self.assertEqual(72, BacktestPolicy(maximum_evaluation_bars=72).maximum_evaluation_bars)
        with self.assertRaises(ValueError):
            BacktestPolicy(maximum_evaluation_bars=0)


if __name__ == "__main__":
    unittest.main()
