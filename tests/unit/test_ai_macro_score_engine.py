from __future__ import annotations

import unittest
from decimal import Decimal

from binance_ai_trader.ai_macro.score_engine import (
    MIN_SCORE,
    MAX_STOP_PCT,
    _ema,
    _atr,
    _score_momentum,
    _score_risk,
    _score_structure,
    _score_trend,
    _score_volume,
    score_candidate,
)
from binance_ai_trader.domain.models import Kline, Ticker24h


def _ticker(symbol: str = "XYZUSDT", change: str = "20", volume: str = "50000000") -> Ticker24h:
    return Ticker24h(
        symbol=symbol,
        quote_volume=Decimal(volume),
        price_change_percent=Decimal(change),
        close_time_ms=1_700_000_000_000,
    )


def _kline(
    close: str = "100",
    high: str | None = None,
    low: str | None = None,
    open_: str = "99",
    i: int = 0,
) -> Kline:
    c = Decimal(close)
    h = Decimal(high) if high else c + Decimal("1")
    lo = Decimal(low) if low else c - Decimal("1")
    return Kline(
        symbol="XYZUSDT", interval="15m",
        open_time_ms=i * 900_000, close_time_ms=(i + 1) * 900_000,
        open=Decimal(open_), high=h, low=lo, close=c,
        volume=Decimal("1000"), quote_volume=Decimal("1000000"),
        trade_count=100,
    )


def _build_klines(n: int = 60, close: str = "100") -> tuple[Kline, ...]:
    return tuple(_kline(close=close, i=i) for i in range(n))


def _build_wide_klines(n: int = 60) -> tuple[Kline, ...]:
    """Klines with very wide ATR (high-low spread of 20 on close=100 → stop > 8%)."""
    klines = []
    for i in range(n):
        klines.append(Kline(
            symbol="XYZUSDT", interval="15m",
            open_time_ms=i * 900_000, close_time_ms=(i + 1) * 900_000,
            open=Decimal("100"), high=Decimal("120"), low=Decimal("80"),
            close=Decimal("100"), volume=Decimal("1000"),
            quote_volume=Decimal("100000"), trade_count=50,
        ))
    return tuple(klines)


class TestScoreDimensions(unittest.TestCase):
    def test_trend_long_above_ema(self) -> None:
        self.assertEqual(_score_trend("LONG", Decimal("102"), Decimal("100")), 20)

    def test_trend_long_slightly_below_ema(self) -> None:
        self.assertEqual(_score_trend("LONG", Decimal("98.5"), Decimal("100")), 15)

    def test_trend_long_far_below_ema(self) -> None:
        self.assertEqual(_score_trend("LONG", Decimal("90"), Decimal("100")), 5)

    def test_trend_short_below_ema(self) -> None:
        self.assertEqual(_score_trend("SHORT", Decimal("98"), Decimal("100")), 20)

    def test_trend_short_slightly_above(self) -> None:
        self.assertEqual(_score_trend("SHORT", Decimal("101"), Decimal("100")), 15)

    def test_trend_short_far_above(self) -> None:
        self.assertEqual(_score_trend("SHORT", Decimal("110"), Decimal("100")), 5)

    def test_momentum_30_plus(self) -> None:
        self.assertEqual(_score_momentum(Decimal("30")), 20)

    def test_momentum_20(self) -> None:
        self.assertEqual(_score_momentum(Decimal("20")), 17)

    def test_momentum_15(self) -> None:
        self.assertEqual(_score_momentum(Decimal("15")), 14)

    def test_momentum_10(self) -> None:
        self.assertEqual(_score_momentum(Decimal("10")), 10)

    def test_momentum_5(self) -> None:
        self.assertEqual(_score_momentum(Decimal("5")), 6)

    def test_momentum_small(self) -> None:
        self.assertEqual(_score_momentum(Decimal("1")), 3)

    def test_volume_100m(self) -> None:
        self.assertEqual(_score_volume(Decimal("100000000")), 20)

    def test_volume_50m(self) -> None:
        self.assertEqual(_score_volume(Decimal("50000000")), 18)

    def test_volume_small(self) -> None:
        self.assertEqual(_score_volume(Decimal("1000000")), 4)

    def test_structure_rr2(self) -> None:
        self.assertEqual(_score_structure(Decimal("2")), 14)

    def test_structure_rr3(self) -> None:
        self.assertEqual(_score_structure(Decimal("3")), 20)

    def test_structure_rr1(self) -> None:
        self.assertEqual(_score_structure(Decimal("1")), 5)

    def test_risk_2pct(self) -> None:
        self.assertEqual(_score_risk(Decimal("2")), 20)

    def test_risk_5pct(self) -> None:
        self.assertEqual(_score_risk(Decimal("5")), 14)

    def test_risk_8pct(self) -> None:
        self.assertEqual(_score_risk(Decimal("8")), 5)

    def test_risk_over_8(self) -> None:
        self.assertEqual(_score_risk(Decimal("9")), 0)

    def test_risk_3pct(self) -> None:
        self.assertEqual(_score_risk(Decimal("3")), 17)

    def test_risk_7pct(self) -> None:
        self.assertEqual(_score_risk(Decimal("7")), 10)


class TestScoreCandidate(unittest.TestCase):
    def test_insufficient_klines_returns_pass(self) -> None:
        klines = _build_klines(10)
        result = score_candidate("XYZUSDT", "LONG", _ticker(), klines)
        self.assertEqual(result.direction, "PASS")
        self.assertEqual(result.reason, "insufficient_klines")

    def test_stop_too_wide_returns_pass(self) -> None:
        klines = _build_wide_klines(60)
        result = score_candidate("XYZUSDT", "LONG", _ticker(), klines)
        self.assertEqual(result.direction, "PASS")
        self.assertIn("stop_too_wide", result.reason)

    def test_score_sums_to_total(self) -> None:
        klines = _build_klines(60)
        result = score_candidate("XYZUSDT", "LONG", _ticker(), klines)
        total = (result.trend_score + result.momentum_score + result.volume_score
                 + result.structure_score + result.risk_score)
        self.assertEqual(result.score, total)

    def test_symbol_preserved(self) -> None:
        klines = _build_klines(60)
        result = score_candidate("SOLUSDT", "LONG", _ticker("SOLUSDT"), klines)
        self.assertEqual(result.symbol, "SOLUSDT")

    def test_direction_preserved_when_viable(self) -> None:
        klines = _build_klines(60)
        result = score_candidate("XYZUSDT", "LONG", _ticker(change="25", volume="100000000"), klines)
        if result.direction != "PASS":
            self.assertEqual(result.direction, "LONG")


class TestHelpers(unittest.TestCase):
    def test_ema_constant_series(self) -> None:
        values = tuple(Decimal("10") for _ in range(25))
        result = _ema(values, 20)
        self.assertAlmostEqual(float(result), 10.0, places=6)

    def test_ema_raises_on_too_short(self) -> None:
        with self.assertRaises(ValueError):
            _ema((Decimal("1"), Decimal("2")), 5)

    def test_atr_raises_on_too_short(self) -> None:
        klines = _build_klines(5)
        with self.assertRaises(ValueError):
            _atr(klines, 14)

    def test_min_score_constant(self) -> None:
        self.assertEqual(MIN_SCORE, 80)

    def test_max_stop_pct_constant(self) -> None:
        self.assertEqual(MAX_STOP_PCT, Decimal("8"))

    def test_ema_increasing_series(self) -> None:
        values = tuple(Decimal(str(i)) for i in range(1, 31))
        result = _ema(values, 20)
        self.assertGreater(float(result), 15.0)


if __name__ == "__main__":
    unittest.main()
