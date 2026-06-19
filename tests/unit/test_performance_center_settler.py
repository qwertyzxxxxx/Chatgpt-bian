import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from binance_ai_trader.performance_center.settler import (
    settle_one, settle_all, _check_candle, _calc_pnl,
)
from binance_ai_trader.performance_center.models import (
    StrategyResult, RESULT_OPEN, RESULT_TP1, RESULT_TP2, RESULT_SL, RESULT_TIMEOUT,
    STRATEGY_HOTLIST,
)


def _make_sr(**kwargs):
    defaults = dict(
        result_id="r1", strategy=STRATEGY_HOTLIST,
        symbol="BTCUSDT", direction="LONG",
        entry="50000", stop_loss="48000", tp1="52000", tp2="54000",
        opened_at="2024-01-01T00:00:00", source_id="hotlist_1",
        result=RESULT_OPEN,
    )
    defaults.update(kwargs)
    return StrategyResult(**defaults)


_NOW = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
_OPENED_AT = "2024-01-01T00:00:00"
_OPENED_MS = 1704067200000
_CLOSE_MS = 1704153600000


def _kline(high, low, close_ms=None):
    if close_ms is None:
        close_ms = _CLOSE_MS
    return [_OPENED_MS, "50100", str(high), str(low), "50500",
            "1000", close_ms, "50000000", 100, "500", "25000000", "0"]


class TestSettlerTPSL(unittest.TestCase):
    def _settle(self, sr, klines):
        with patch("binance_ai_trader.performance_center.settler._fetch_klines", return_value=klines):
            return settle_one(sr, now=_NOW)

    def test_tp1_long(self):
        sr = _make_sr()
        result = self._settle(sr, [_kline(high=52100, low=49000)])
        self.assertEqual(result.result, RESULT_TP1)
        self.assertIsNotNone(result.pnl_pct)
        self.assertIsNotNone(result.rr_realized)

    def test_tp2_long(self):
        sr = _make_sr()
        result = self._settle(sr, [_kline(high=54100, low=49000)])
        self.assertEqual(result.result, RESULT_TP2)

    def test_sl_long(self):
        sr = _make_sr()
        result = self._settle(sr, [_kline(high=50500, low=47900)])
        self.assertEqual(result.result, RESULT_SL)
        self.assertLess(result.pnl_pct, 0)

    def test_tp1_short(self):
        sr = _make_sr(direction="SHORT", entry="50000", stop_loss="52000",
                      tp1="48000", tp2="46000")
        result = self._settle(sr, [_kline(high=50500, low=47900)])
        self.assertEqual(result.result, RESULT_TP1)

    def test_sl_short(self):
        sr = _make_sr(direction="SHORT", entry="50000", stop_loss="52000",
                      tp1="48000", tp2="46000")
        result = self._settle(sr, [_kline(high=52100, low=49000)])
        self.assertEqual(result.result, RESULT_SL)

    def test_no_trigger_stays_open(self):
        sr = _make_sr()
        result = self._settle(sr, [_kline(high=51000, low=49000)])
        self.assertEqual(result.result, RESULT_OPEN)

    def test_sl_before_tp(self):
        sr = _make_sr()
        k1 = _kline(high=50500, low=47900, close_ms=1704067215000)
        k2 = _kline(high=52100, low=49000, close_ms=1704153600000)
        result = self._settle(sr, [k1, k2])
        self.assertEqual(result.result, RESULT_SL)

    def test_timeout(self):
        far_future = datetime(2025, 1, 10, tzinfo=timezone.utc)
        sr = _make_sr()
        result = self._settle(sr, [])
        sr2 = settle_one(sr, now=far_future)
        self.assertEqual(sr2.result, RESULT_TIMEOUT)

    def test_already_settled_skipped(self):
        sr = _make_sr(result=RESULT_TP1)
        with patch("binance_ai_trader.performance_center.settler._fetch_klines") as m:
            settle_one(sr)
            m.assert_not_called()

    def test_invalid_direction_skipped(self):
        sr = _make_sr(direction="UNKNOWN")
        result = self._settle(sr, [_kline(54100, 47900)])
        self.assertEqual(result.result, RESULT_OPEN)


class TestCheckCandle(unittest.TestCase):
    _T = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_long_sl_wins(self):
        r, _ = _check_candle("LONG", 52100, 47900, 48000, 52000, 54000, self._T)
        self.assertEqual(r, RESULT_SL)

    def test_long_tp1_no_sl(self):
        r, _ = _check_candle("LONG", 52100, 49000, 48000, 52000, 54000, self._T)
        self.assertEqual(r, RESULT_TP1)

    def test_long_tp2(self):
        r, _ = _check_candle("LONG", 54100, 49000, 48000, 52000, 54000, self._T)
        self.assertEqual(r, RESULT_TP2)

    def test_short_sl(self):
        r, _ = _check_candle("SHORT", 52100, 49000, 52000, 48000, 46000, self._T)
        self.assertEqual(r, RESULT_SL)


class TestCalcPnl(unittest.TestCase):
    def test_tp1_long(self):
        pnl, rr = _calc_pnl("LONG", 50000, 48000, 52000, 54000, RESULT_TP1)
        self.assertGreater(pnl, 0)
        self.assertAlmostEqual(rr, 1.0)

    def test_sl_long(self):
        pnl, rr = _calc_pnl("LONG", 50000, 48000, 52000, 54000, RESULT_SL)
        self.assertLess(pnl, 0)
        self.assertEqual(rr, -1.0)

    def test_zero_risk(self):
        pnl, rr = _calc_pnl("LONG", 50000, 50000, 52000, 54000, RESULT_TP1)
        self.assertIsNone(pnl)
        self.assertIsNone(rr)


class TestSettleAll(unittest.TestCase):
    def test_settle_all_empty(self):
        self.assertEqual(settle_all([]), [])

    def test_settle_all_multiple(self):
        srs = [_make_sr(result_id=f"r{i}", source_id=f"h_{i}") for i in range(3)]
        with patch("binance_ai_trader.performance_center.settler._fetch_klines",
                   return_value=[_kline(high=52100, low=49000)]):
            results = settle_all(srs, now=_NOW)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r.result, RESULT_TP1)


class TestParseOpenedAt(unittest.TestCase):
    """opened_at timezone format coverage."""

    from binance_ai_trader.performance_center.settler import _parse_opened_at as _p

    def _p(self, s: str):
        from binance_ai_trader.performance_center.settler import _parse_opened_at
        return _parse_opened_at(s)

    def test_plain_iso_no_tz(self):
        dt = self._p("2026-06-15T09:18:06")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.hour, 9)

    def test_plus_zero_offset(self):
        dt = self._p("2026-06-15T09:18:06+00:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.hour, 9)

    def test_z_suffix(self):
        dt = self._p("2026-06-15T09:18:06Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.hour, 9)

    def test_microseconds_with_offset(self):
        dt = self._p("2026-06-15T09:18:06.123456+00:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.microsecond, 123456)

    def test_microseconds_z(self):
        dt = self._p("2026-06-15T09:18:06.000000Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_space_separator(self):
        dt = self._p("2026-06-15 09:18:06")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 9)

    def test_garbage_returns_none(self):
        dt = self._p("not-a-date")
        self.assertIsNone(dt)

    def test_empty_returns_none(self):
        dt = self._p("")
        self.assertIsNone(dt)

    def test_settle_one_with_plus_offset_doesnt_skip(self):
        sr = _make_sr(opened_at="2024-01-01T00:00:00+00:00")
        with patch("binance_ai_trader.performance_center.settler._fetch_klines",
                   return_value=[_kline(high=52100, low=49000)]):
            result = settle_one(sr, now=_NOW)
        self.assertEqual(result.result, RESULT_TP1)

    def test_settle_one_with_z_suffix_doesnt_skip(self):
        sr = _make_sr(opened_at="2024-01-01T00:00:00Z")
        with patch("binance_ai_trader.performance_center.settler._fetch_klines",
                   return_value=[_kline(high=52100, low=49000)]):
            result = settle_one(sr, now=_NOW)
        self.assertEqual(result.result, RESULT_TP1)


if __name__ == "__main__":
    unittest.main()
