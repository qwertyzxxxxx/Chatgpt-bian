"""Tests for the hotlist_reversal (V-Reversal) strategy: indicators, signal
generation, and settler-side movable stop-loss / TIMEOUT_FORCED handling."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from binance_ai_trader.v3.strategies import reversal_indicators as ind
from binance_ai_trader.v3.strategies.reversal import (
    MAIN_COIN_BLACKLIST,
    HotlistStrategyReversal,
)
from binance_ai_trader.v3.paper.repository import V3PaperOrder
from binance_ai_trader.v3.settlement.settler import V3Settler


def _dec_range(start: float, n: int, step: float = 1.0) -> list[Decimal]:
    return [Decimal(str(start + i * step)) for i in range(n)]


# ─────────────────────────────────────────────────────────────────────────────
# Indicators
# ─────────────────────────────────────────────────────────────────────────────

def test_atr_basic():
    highs = _dec_range(10, 20, 0.5)
    lows = [h - Decimal("1") for h in highs]
    closes = [h - Decimal("0.5") for h in highs]
    result = ind.atr(highs, lows, closes, period=14)
    assert result is not None
    assert result > 0


def test_atr_insufficient_data_returns_none():
    assert ind.atr([Decimal(1)], [Decimal(1)], [Decimal(1)], period=14) is None


def test_rsi_all_gains_is_100():
    closes = _dec_range(1, 20, 1.0)  # strictly increasing
    assert ind.rsi(closes, period=14) == Decimal(100)


def test_rsi_mixed():
    closes = [Decimal(v) for v in [10, 11, 9, 12, 8, 13, 7, 14, 6, 15, 5, 16, 4, 17, 3]]
    r = ind.rsi(closes, period=14)
    assert r is not None
    assert 0 <= r <= 100


def test_bollinger_3x_wider_than_default():
    closes = _dec_range(100, 25, 0.3)
    b3 = ind.bollinger(closes, period=20, num_std=Decimal("3"))
    b2 = ind.bollinger(closes, period=20, num_std=Decimal("2"))
    assert b3 is not None and b2 is not None
    assert (b3[1] - b3[0]) > (b2[1] - b2[0])  # 3-sigma band wider than 2-sigma


def test_wick_ratio_gravestone():
    # long upper wick, tiny body, near-zero lower wick
    upper, lower = ind.wick_ratio(Decimal(100), Decimal(110), Decimal(99.5), Decimal(100.2))
    assert upper > Decimal("0.6")
    assert lower < Decimal("0.1")


def test_volume_ma_excludes_current_bar():
    volumes = [Decimal(10)] * 20 + [Decimal(1000)]
    vma = ind.volume_ma(volumes, period=20)
    assert vma == Decimal(10)


def test_oi_drop_pct_negative_on_decline():
    points = [Decimal(1000), Decimal(950), Decimal(880)]
    drop = ind.oi_drop_pct(points)
    assert drop is not None
    assert drop < 0


# ─────────────────────────────────────────────────────────────────────────────
# Strategy universe selection
# ─────────────────────────────────────────────────────────────────────────────

class _FakeTicker:
    def __init__(self, symbol, quote_volume):
        self.symbol = symbol
        self.quote_volume = quote_volume


def test_universe_excludes_main_coins_and_out_of_range_volume():
    strategy = HotlistStrategyReversal(client=MagicMock())
    tickers = [
        _FakeTicker("BTCUSDT", Decimal("500000000")),   # blacklisted main coin
        _FakeTicker("DOGEUSDT", Decimal("50000000")),   # blacklisted main coin
        _FakeTicker("SOMEALTUSDT", Decimal("50000000")),  # in range
        _FakeTicker("TOOSMALLUSDT", Decimal("1000000")),  # too small
        _FakeTicker("TOOBIGUSDT", Decimal("500000000")),  # too big
        _FakeTicker("BUSDT", Decimal("40000000")),  # non-USDT-perp naming edge case, still passes suffix check
    ]
    universe = strategy._select_universe(tickers)
    assert "SOMEALTUSDT" in universe
    assert "BTCUSDT" not in universe
    assert "DOGEUSDT" not in universe
    assert "TOOSMALLUSDT" not in universe
    assert "TOOBIGUSDT" not in universe


def test_main_coin_blacklist_has_no_duplicates_and_is_frozen():
    assert isinstance(MAIN_COIN_BLACKLIST, frozenset)
    assert len(MAIN_COIN_BLACKLIST) >= 30


# ─────────────────────────────────────────────────────────────────────────────
# Settler: movable stop-loss (breakeven) + TIMEOUT_FORCED
# ─────────────────────────────────────────────────────────────────────────────

def _make_order(**overrides) -> V3PaperOrder:
    now = datetime.now(UTC)
    base = dict(
        order_id="o1",
        signal_id="REV-20260710-000001",
        strategy_id="hotlist_reversal",
        symbol="SOMEALTUSDT",
        direction="LONG",
        entry=Decimal("10"),
        stop_loss=Decimal("9"),      # 1.0 risk
        tp1=Decimal("12.2"),         # 2.2 RR
        tp2=Decimal("12.2"),
        rr=Decimal("2.2"),
        status="FILLED",
        result=None,
        created_at=now.isoformat(timespec="seconds"),
        filled_at=now.isoformat(timespec="seconds"),
        closed_at=None,
        expires_at=(now + timedelta(minutes=240)).isoformat(timespec="seconds"),
        pnl_pct=None,
        rr_realized=None,
        pushed=True,
        metadata_json=json.dumps({
            "max_hold_minutes": 240,
            "breakeven_trigger_r": "0.7",
            "orig_stop_loss": "9",
            "breakeven_activated": False,
        }),
    )
    base.update(overrides)
    return V3PaperOrder(**base)


class _FakeKline:
    def __init__(self, high, low, close):
        self.high = Decimal(str(high))
        self.low = Decimal(str(low))
        self.close = Decimal(str(close))
        self.open_time_ms = 0
        self.close_time_ms = 1


def test_breakeven_not_activated_before_min_hold_time():
    order = _make_order(filled_at=datetime.now(UTC).isoformat(timespec="seconds"))
    client = MagicMock()
    # price already moved 0.7x risk in favor (10 -> 10.7), but held < 5 minutes
    client.klines.return_value = [_FakeKline(10.7, 10.5, 10.7)]
    repo = MagicMock()
    settler = V3Settler(repo, client)

    changed = settler._settle_one(order, datetime.now(UTC) + timedelta(seconds=30))

    assert changed is False
    repo.update_stop_loss.assert_not_called()


def test_breakeven_activates_after_min_hold_and_favorable_move():
    filled_at = datetime.now(UTC) - timedelta(minutes=6)
    order = _make_order(filled_at=filled_at.isoformat(timespec="seconds"))
    client = MagicMock()
    client.klines.return_value = [_FakeKline(10.75, 10.6, 10.75)]  # +0.75 >= 0.7 risk
    repo = MagicMock()
    settler = V3Settler(repo, client)

    changed = settler._settle_one(order, datetime.now(UTC))

    assert changed is True
    repo.update_stop_loss.assert_called_once_with("o1", Decimal("10"))
    repo.update_metadata.assert_called_once()
    saved_meta = json.loads(repo.update_metadata.call_args[0][1])
    assert saved_meta["breakeven_activated"] is True


def test_forced_close_at_max_hold_minutes():
    filled_at = datetime.now(UTC) - timedelta(minutes=241)
    order = _make_order(filled_at=filled_at.isoformat(timespec="seconds"))
    client = MagicMock()
    client.klines.return_value = [_FakeKline(10.3, 10.1, 10.3)]
    repo = MagicMock()
    settler = V3Settler(repo, client)

    changed = settler._settle_one(order, datetime.now(UTC))

    assert changed is True
    repo.update_settled.assert_called_once()
    args = repo.update_settled.call_args[0]
    assert args[0] == "o1"
    assert args[1] == "TIMEOUT_FORCED"


def test_normal_sl_still_applies_after_breakeven_metadata_present():
    order = _make_order()  # stop_loss=9 (not yet moved), breakeven not active
    client = MagicMock()
    client.klines.return_value = [_FakeKline(10.1, 8.5, 8.9)]  # low breaches SL=9
    repo = MagicMock()
    settler = V3Settler(repo, client)

    changed = settler._settle_one(order, datetime.now(UTC))

    assert changed is True
    repo.update_settled.assert_called_once()
    assert repo.update_settled.call_args[0][1] == "SL"


def test_non_reversal_orders_are_unaffected_by_reversal_logic():
    order = _make_order(strategy_id="hotlist_momentum_v3", metadata_json="{}")
    client = MagicMock()
    client.klines.return_value = [_FakeKline(9.5, 9.4, 9.45)]  # neither TP nor SL hit
    repo = MagicMock()
    settler = V3Settler(repo, client)

    changed = settler._settle_one(order, datetime.now(UTC))

    assert changed is False
    repo.update_stop_loss.assert_not_called()
