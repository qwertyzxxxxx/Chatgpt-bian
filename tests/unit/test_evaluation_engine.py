from decimal import Decimal
import unittest

from binance_ai_trader.domain.models import Kline, StoredSignal
from binance_ai_trader.evaluation import SignalEvaluationEngine


def stored_signal(symbol: str = "BTCUSDT", direction: str = "LONG") -> StoredSignal:
    short = direction == "SHORT"
    return StoredSignal(
        run_id="run-1",
        symbol=symbol,
        direction=direction,
        entry=Decimal("100"),
        stop_loss=Decimal("105" if short else "95"),
        tp1=Decimal("95" if short else "105"),
        tp2=Decimal("90" if short else "110"),
        generated_at="1970-01-01T00:00:01+00:00",
        generated_at_ms=1000,
    )


def bar(index: int, low: str = "99", high: str = "101", symbol: str = "BTCUSDT") -> Kline:
    open_time = 2000 + index * 900_000
    return Kline(
        symbol=symbol,
        interval="15m",
        open_time_ms=open_time,
        close_time_ms=open_time + 899_999,
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("100"),
        volume=Decimal("1"),
        quote_volume=Decimal("100"),
        trade_count=1,
    )


class SignalEvaluationEngineTest(unittest.TestCase):
    def test_same_bar_stop_and_target_is_conservative_loss(self) -> None:
        result = SignalEvaluationEngine().evaluate(stored_signal(), (bar(0, low="94", high="111"),))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("LOSS", result.result)
        self.assertEqual(1, result.bars_to_result)
        self.assertEqual(Decimal("11.00"), result.max_favorable_pct)
        self.assertEqual(Decimal("6.00"), result.max_adverse_pct)

    def test_tp2_is_win_and_records_bar_count(self) -> None:
        bars = (bar(0, high="106"), bar(1, high="111"))
        result = SignalEvaluationEngine().evaluate(stored_signal(), bars)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("WIN_TP2", result.result)
        self.assertEqual(2, result.bars_to_result)

    def test_tp1_hit_is_final_after_complete_96_bar_window(self) -> None:
        bars = tuple(bar(index, high="106" if index == 4 else "101") for index in range(96))
        result = SignalEvaluationEngine().evaluate(stored_signal(), bars)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("TP1_HIT", result.result)
        self.assertEqual(5, result.bars_to_result)

    def test_no_trigger_expires_after_96_bars(self) -> None:
        result = SignalEvaluationEngine().evaluate(stored_signal(), tuple(bar(index) for index in range(96)))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("EXPIRED", result.result)
        self.assertEqual(96, result.bars_to_result)

    def test_incomplete_window_without_terminal_result_remains_pending(self) -> None:
        self.assertIsNone(SignalEvaluationEngine().evaluate(stored_signal(), tuple(bar(index) for index in range(95))))

    def test_levels_are_ignored_until_entry_is_touched(self) -> None:
        bars = (bar(0, low="101", high="111"), bar(1, low="99", high="101"), bar(2, low="94", high="101"))
        result = SignalEvaluationEngine().evaluate(stored_signal(), bars)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("LOSS", result.result)
        self.assertEqual(3, result.bars_to_result)

    def test_short_tp2_is_win(self) -> None:
        result = SignalEvaluationEngine().evaluate(
            stored_signal(direction="SHORT"), (bar(0, low="94"), bar(1, low="89"))
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("SHORT", result.direction)
        self.assertEqual("WIN_TP2", result.result)
        self.assertEqual(2, result.bars_to_result)

    def test_short_tp1_is_final_after_complete_window(self) -> None:
        bars = tuple(bar(index, low="94" if index == 4 else "99") for index in range(96))
        result = SignalEvaluationEngine().evaluate(stored_signal(direction="SHORT"), bars)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("TP1_HIT", result.result)
        self.assertEqual(5, result.bars_to_result)

    def test_short_stop_is_loss(self) -> None:
        result = SignalEvaluationEngine().evaluate(
            stored_signal(direction="SHORT"), (bar(0, high="106"),)
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("LOSS", result.result)
        self.assertEqual(Decimal("6.00"), result.max_adverse_pct)

    def test_short_expires_after_complete_window(self) -> None:
        result = SignalEvaluationEngine().evaluate(
            stored_signal(direction="SHORT"), tuple(bar(index) for index in range(96))
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("EXPIRED", result.result)

    def test_short_same_bar_target_and_stop_is_loss(self) -> None:
        result = SignalEvaluationEngine().evaluate(
            stored_signal(direction="SHORT"), (bar(0, low="89", high="106"),)
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("LOSS", result.result)
        self.assertEqual(Decimal("11.00"), result.max_favorable_pct)
        self.assertEqual(Decimal("6.00"), result.max_adverse_pct)


if __name__ == "__main__":
    unittest.main()
