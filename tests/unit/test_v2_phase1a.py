"""V2 Phase 1A tests — 13 specs covering the core closed loop.

T1:  hotlist_momentum_v2 is registered in Strategy Registry
T2:  Strategy outputs V2Signal (not HotlistEntryPlan)
T3:  Signal 24-hour dedup — same strategy/symbol/direction not duplicated
T4:  Risk engine blocks when max_open_orders reached
T5:  Paper order is created from signal (CREATED event appended)
T6:  LONG: entry not touched → EXPIRED_NOT_FILLED (never TP/SL)
T7:  SHORT: entry not touched → EXPIRED_NOT_FILLED (never TP/SL)
T8:  LONG: entry touched → TP/SL evaluated from post-fill candles only
T9:  SHORT: entry touched → TP/SL evaluated from post-fill candles only
T10: EXPIRED_NOT_FILLED not in win-rate denominator
T11: Performance reads only v2_paper_orders (not V1 tables)
T12: Telegram messages carry [V2] prefix
T13: V2 tables created without touching V1 tables
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.v2.order_events.repository import (
    V2OrderEvent, V2OrderEventRepository, make_event_id,
)
from binance_ai_trader.v2.paper_portfolio.repository import (
    V2PaperOrder, V2PaperOrderRepository, make_order_id,
)
from binance_ai_trader.v2.performance.calculator import V2PerformanceCalculator
from binance_ai_trader.v2.risk.engine import V2RiskEngine
from binance_ai_trader.v2.settlement.settler import V2Settler
from binance_ai_trader.v2.signals.repository import V2Signal, V2SignalRepository, make_signal_id
from binance_ai_trader.v2.strategy_registry.repository import (
    V2Strategy, V2StrategyRepository, register_hotlist_momentum_v2,
)
from binance_ai_trader.v2.strategies.hotlist_momentum import HotlistMomentumV2
from binance_ai_trader.v2.telegram.notifier import V2TelegramNotifier


# ── helpers ────────────────────────────────────────────────────────────────

def _db() -> Path:
    tmp = tempfile.mktemp(suffix=".db")
    return Path(tmp)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _expires_iso(hours: int = 24) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat(timespec="seconds")


def _past_iso(hours: int = 25) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")


def _make_kline(
    low: str,
    high: str,
    close: str = "100",
    close_time_ms: int | None = None,
) -> Kline:
    ts = close_time_ms or int(datetime.now(UTC).timestamp() * 1000) + 1_000_000
    return Kline(
        symbol="BTCUSDT",
        interval="15m",
        open_time_ms=ts - 900_000,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        close_time_ms=ts,
        quote_volume=Decimal("100000"),
        trade_count=100,
    )


def _open_order(
    db: Path,
    direction: str = "LONG",
    entry: str = "100",
    sl: str = "95",
    tp1: str = "105",
    tp2: str = "110",
    strategy_id: str = "hotlist_momentum_v2",
    created_hours_ago: int = 0,
    expires_hours: int = 24,
) -> V2PaperOrder:
    repo = V2PaperOrderRepository(db)
    repo.ensure_table()
    now = datetime.now(UTC) - timedelta(hours=created_hours_ago)
    order = V2PaperOrder(
        order_id=make_order_id(),
        signal_id=make_signal_id(),
        strategy_id=strategy_id,
        symbol="BTCUSDT",
        direction=direction,
        entry=Decimal(entry),
        stop_loss=Decimal(sl),
        tp1=Decimal(tp1),
        tp2=Decimal(tp2),
        rr=Decimal("2"),
        status="OPEN",
        result=None,
        created_at=now.isoformat(timespec="seconds"),
        filled_at=None,
        closed_at=None,
        expires_at=(now + timedelta(hours=expires_hours)).isoformat(timespec="seconds"),
        pnl_pct=None,
        rr_realized=None,
        duration_minutes=None,
        pushed=True,
        metadata_json="{}",
    )
    repo.save(order)
    return order


def _settler(db: Path, klines: list[Kline]) -> V2Settler:
    order_repo = V2PaperOrderRepository(db)
    event_repo = V2OrderEventRepository(db)
    order_repo.ensure_table()
    event_repo.ensure_table()
    client = MagicMock()
    client.klines.return_value = klines
    return V2Settler(order_repo, event_repo, client)


# ── T1: Strategy Registry ──────────────────────────────────────────────────

def test_t1_strategy_registry_registers_hotlist_v2():
    db = _db()
    strategy = register_hotlist_momentum_v2(db)
    assert strategy.strategy_id == "hotlist_momentum_v2"
    assert strategy.enabled is True
    assert strategy.status == "PAPER"

    repo = V2StrategyRepository(db)
    fetched = repo.get("hotlist_momentum_v2")
    assert fetched is not None
    assert fetched.strategy_id == "hotlist_momentum_v2"
    assert fetched.max_open_orders == 3


# ── T2: Strategy outputs V2Signal ─────────────────────────────────────────

def test_t2_strategy_outputs_v2signal():
    db = _db()
    strategy = register_hotlist_momentum_v2(db)
    signal_repo = V2SignalRepository(db)
    signal_repo.ensure_table()

    from binance_ai_trader.hotlist.models import HotlistEntryPlan
    mock_plan = HotlistEntryPlan(
        symbol="ETHUSDT", direction="LONG",
        current_price=Decimal("3000"), change_24h_pct=Decimal("18"),
        quote_volume=Decimal("10000000"), volume_ratio_15m=Decimal("1.5"),
        ema20_15m=Decimal("2990"), atr14=Decimal("50"),
        swing_high=Decimal("3100"), swing_low=Decimal("2950"),
        suggested_limit_entry=Decimal("2990"), stop_loss=Decimal("2940"),
        tp1=Decimal("3040"), tp2=Decimal("3090"),
        rr=Decimal("2.0"),
        expires_at=_expires_iso(),
        reason="test reason", sentiment="🔥",
    )

    client = MagicMock()
    universe_cfg = MagicMock()

    strat = HotlistMomentumV2(client, universe_cfg, signal_repo, strategy)
    with patch.object(strat, '_client'), \
         patch('binance_ai_trader.v2.strategies.hotlist_momentum.HotlistWatcher') as MockWatcher:
        MockWatcher.return_value.watch.return_value = (mock_plan,)
        signals = strat.scan()

    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, V2Signal)
    assert sig.strategy_id == "hotlist_momentum_v2"
    assert sig.symbol == "ETHUSDT"
    assert sig.direction == "LONG"
    assert sig.entry == Decimal("2990")


# ── T3: Signal 24h dedup ──────────────────────────────────────────────────

def test_t3_signal_24h_dedup():
    db = _db()
    strategy = register_hotlist_momentum_v2(db)
    signal_repo = V2SignalRepository(db)
    signal_repo.ensure_table()

    from binance_ai_trader.hotlist.models import HotlistEntryPlan
    mock_plan = HotlistEntryPlan(
        symbol="SOLUSDT", direction="LONG",
        current_price=Decimal("150"), change_24h_pct=Decimal("20"),
        quote_volume=Decimal("8000000"), volume_ratio_15m=Decimal("1.3"),
        ema20_15m=Decimal("148"), atr14=Decimal("3"),
        swing_high=Decimal("155"), swing_low=Decimal("145"),
        suggested_limit_entry=Decimal("148"), stop_loss=Decimal("145"),
        tp1=Decimal("151"), tp2=Decimal("154"), rr=Decimal("2.0"),
        expires_at=_expires_iso(), reason="momentum", sentiment="🚀",
    )

    strat = HotlistMomentumV2(MagicMock(), MagicMock(), signal_repo, strategy)
    with patch('binance_ai_trader.v2.strategies.hotlist_momentum.HotlistWatcher') as MockWatcher:
        MockWatcher.return_value.watch.return_value = (mock_plan,)
        first = strat.scan()

    assert len(first) == 1

    with patch('binance_ai_trader.v2.strategies.hotlist_momentum.HotlistWatcher') as MockWatcher:
        MockWatcher.return_value.watch.return_value = (mock_plan,)
        second = strat.scan()

    assert len(second) == 0, "dedup should suppress same symbol/direction within 24h"


# ── T4: Risk engine max_open_orders ───────────────────────────────────────

def test_t4_risk_engine_blocks_max_open_orders():
    db = _db()
    strategy = register_hotlist_momentum_v2(db)

    for i in range(3):
        order = _open_order(db, strategy_id="hotlist_momentum_v2")

    signal_repo = V2SignalRepository(db)
    signal_repo.ensure_table()
    signal = V2Signal(
        signal_id=make_signal_id(), strategy_id="hotlist_momentum_v2",
        symbol="XRPUSDT", direction="LONG",
        entry=Decimal("0.5"), stop_loss=Decimal("0.475"),
        tp1=Decimal("0.525"), tp2=Decimal("0.55"), rr=Decimal("2"),
        reason="", metadata_json="{}", created_at=_now_iso(),
    )

    order_repo = V2PaperOrderRepository(db)
    risk = V2RiskEngine(order_repo)
    decision = risk.check(signal, strategy)
    assert decision.allowed is False
    assert "max_open_orders" in decision.reason


# ── T5: Paper order created from signal + CREATED event ───────────────────

def test_t5_paper_order_created_from_signal():
    db = _db()
    order_repo = V2PaperOrderRepository(db)
    event_repo = V2OrderEventRepository(db)
    order_repo.ensure_table()
    event_repo.ensure_table()

    signal = V2Signal(
        signal_id=make_signal_id(), strategy_id="hotlist_momentum_v2",
        symbol="BNBUSDT", direction="SHORT",
        entry=Decimal("600"), stop_loss=Decimal("630"),
        tp1=Decimal("570"), tp2=Decimal("540"), rr=Decimal("2"),
        reason="test", metadata_json="{}", created_at=_now_iso(),
    )
    now = datetime.now(UTC)
    order = V2PaperOrder(
        order_id=make_order_id(), signal_id=signal.signal_id,
        strategy_id=signal.strategy_id, symbol=signal.symbol,
        direction=signal.direction, entry=signal.entry,
        stop_loss=signal.stop_loss, tp1=signal.tp1, tp2=signal.tp2,
        rr=signal.rr, status="OPEN", result=None,
        created_at=now.isoformat(timespec="seconds"),
        filled_at=None, closed_at=None,
        expires_at=(now + timedelta(hours=24)).isoformat(timespec="seconds"),
        pnl_pct=None, rr_realized=None, duration_minutes=None,
        pushed=True, metadata_json="{}",
    )
    order_repo.save(order)
    event_repo.append(V2OrderEvent(
        event_id=make_event_id(), order_id=order.order_id,
        event_type="CREATED", old_status=None, new_status="OPEN",
        candle_high=None, candle_low=None,
        triggered_at=now.isoformat(timespec="seconds"), metadata_json="{}",
    ))

    saved = [o for o in order_repo.load_all() if o.order_id == order.order_id]
    assert len(saved) == 1
    assert saved[0].status == "OPEN"
    assert saved[0].signal_id == signal.signal_id

    events = event_repo.load_for_order(order.order_id)
    assert len(events) == 1
    assert events[0].event_type == "CREATED"
    assert events[0].new_status == "OPEN"


# ── T6: LONG — entry never touched → EXPIRED_NOT_FILLED ──────────────────

def test_t6_long_no_fill_expires_not_filled():
    db = _db()
    # LONG order: entry=100, expires in past, no candle.low <= 100
    order = _open_order(db, direction="LONG", entry="100", sl="95",
                        tp1="105", tp2="110", created_hours_ago=25, expires_hours=24)
    # All candles have low > 100 (entry never touched)
    klines = [_make_kline(low="101", high="108", close_time_ms=int(
        (datetime.now(UTC) - timedelta(hours=24-i)).timestamp() * 1000
    )) for i in range(10)]
    settler = _settler(db, klines)
    settler.settle_all()

    order_repo = V2PaperOrderRepository(db)
    orders = order_repo.load_all()
    assert len(orders) == 1
    assert orders[0].result == "EXPIRED_NOT_FILLED"
    assert orders[0].status == "EXPIRED_NOT_FILLED"


# ── T7: SHORT — entry never touched → EXPIRED_NOT_FILLED ─────────────────

def test_t7_short_no_fill_expires_not_filled():
    db = _db()
    # SHORT order: entry=100, expires in past, no candle.high >= 100
    order = _open_order(db, direction="SHORT", entry="100", sl="105",
                        tp1="95", tp2="90", created_hours_ago=25, expires_hours=24)
    # All candles have high < 100 (entry never touched)
    klines = [_make_kline(low="88", high="99", close_time_ms=int(
        (datetime.now(UTC) - timedelta(hours=24-i)).timestamp() * 1000
    )) for i in range(10)]
    settler = _settler(db, klines)
    settler.settle_all()

    order_repo = V2PaperOrderRepository(db)
    orders = order_repo.load_all()
    assert orders[0].result == "EXPIRED_NOT_FILLED"


# ── T8: LONG — entry touched → TP evaluated from post-fill candles ────────

def test_t8_long_fill_then_tp1():
    db = _db()
    now = datetime.now(UTC)
    order = _open_order(db, direction="LONG", entry="100", sl="95",
                        tp1="105", tp2="110")
    # Candle 1: low=99 (touches entry → FILLED)
    # Candle 2 (post-fill): high=106 (hits TP1)
    t0 = int(now.timestamp() * 1000) + 1_000
    fill_candle = _make_kline(low="99", high="102", close_time_ms=t0)
    tp_candle = _make_kline(low="103", high="106", close_time_ms=t0 + 900_000)
    settler = _settler(db, [fill_candle, tp_candle])
    settler.settle_all()

    order_repo = V2PaperOrderRepository(db)
    orders = order_repo.load_all()
    assert orders[0].result == "TP1"

    event_repo = V2OrderEventRepository(db)
    events = event_repo.load_for_order(orders[0].order_id)
    event_types = [e.event_type for e in events]
    assert "FILLED" in event_types
    assert "TP1" in event_types


# ── T9: SHORT — entry touched → SL evaluated from post-fill candles ───────

def test_t9_short_fill_then_sl():
    db = _db()
    now = datetime.now(UTC)
    order = _open_order(db, direction="SHORT", entry="100", sl="106",
                        tp1="94", tp2="88")
    # Candle 1: high=101 (touches entry → FILLED)
    # Candle 2 (post-fill): high=107 (hits SL)
    t0 = int(now.timestamp() * 1000) + 1_000
    fill_candle = _make_kline(low="98", high="101", close_time_ms=t0)
    sl_candle = _make_kline(low="104", high="107", close_time_ms=t0 + 900_000)
    settler = _settler(db, [fill_candle, sl_candle])
    settler.settle_all()

    order_repo = V2PaperOrderRepository(db)
    orders = order_repo.load_all()
    assert orders[0].result == "SL"


# ── T10: EXPIRED_NOT_FILLED not in win-rate denominator ──────────────────

def test_t10_expired_not_filled_excluded_from_win_rate():
    db = _db()
    order_repo = V2PaperOrderRepository(db)
    order_repo.ensure_table()

    def _settled(result: str, pnl: str | None = None, rr: str | None = None) -> V2PaperOrder:
        now = datetime.now(UTC)
        o = V2PaperOrder(
            order_id=make_order_id(), signal_id=make_signal_id(),
            strategy_id="hotlist_momentum_v2", symbol="BTCUSDT",
            direction="LONG", entry=Decimal("100"),
            stop_loss=Decimal("95"), tp1=Decimal("105"),
            tp2=Decimal("110"), rr=Decimal("2"),
            status=result, result=result,
            created_at=now.isoformat(timespec="seconds"),
            filled_at=now.isoformat(timespec="seconds"),
            closed_at=now.isoformat(timespec="seconds"),
            expires_at=(now + timedelta(hours=24)).isoformat(timespec="seconds"),
            pnl_pct=Decimal(pnl) if pnl else None,
            rr_realized=Decimal(rr) if rr else None,
            duration_minutes=60,
            pushed=True, metadata_json="{}",
        )
        order_repo.save(o)
        return o

    _settled("TP1", pnl="5", rr="1.0")
    _settled("SL", pnl="-5", rr="-1.0")
    _settled("EXPIRED_NOT_FILLED")

    calc = V2PerformanceCalculator(order_repo)
    perf = calc.calculate("hotlist_momentum_v2")

    assert perf.tp1 == 1
    assert perf.sl == 1
    assert perf.expired_not_filled == 1
    assert perf.win_rate == Decimal("50.00"), f"expected 50.00, got {perf.win_rate}"


# ── T11: Performance reads only v2_paper_orders ───────────────────────────

def test_t11_performance_reads_only_v2_paper_orders():
    db = _db()
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE hotlist_opportunities (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE strategy_results (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    order_repo = V2PaperOrderRepository(db)
    order_repo.ensure_table()

    calc = V2PerformanceCalculator(order_repo)
    perf = calc.calculate()

    assert perf.orders == 0
    assert perf.win_rate == Decimal("0.00")


# ── T12: Telegram messages carry [V2] prefix ─────────────────────────────

def test_t12_telegram_messages_carry_v2_prefix():
    sent: list[str] = []
    mock_notifier = MagicMock()
    mock_notifier.send.side_effect = lambda msg: sent.append(msg)

    v2_tg = V2TelegramNotifier(mock_notifier)

    signal = V2Signal(
        signal_id="sig-x", strategy_id="hotlist_momentum_v2",
        symbol="ETHUSDT", direction="LONG",
        entry=Decimal("3000"), stop_loss=Decimal("2850"),
        tp1=Decimal("3150"), tp2=Decimal("3300"),
        rr=Decimal("2"),
        reason="test", metadata_json="{}",
        created_at=_now_iso(),
    )
    v2_tg.send_signal(signal)
    assert len(sent) == 1
    assert "[V2]" in sent[0]
    assert "Hotlist Momentum Signal" in sent[0]

    from binance_ai_trader.v2.performance.calculator import V2Performance
    perf = V2Performance(
        strategy_id="hotlist_momentum_v2",
        orders=5, filled=3, not_filled=1, open_count=1,
        tp1=2, tp2=0, sl=1, expired_not_filled=1,
        win_rate=Decimal("66.67"), avg_rr=Decimal("1.5"), avg_pnl=Decimal("3.2"),
    )
    v2_tg.send_summary(perf)
    assert len(sent) == 2
    assert "[V2]" in sent[1]
    assert "Paper Portfolio Summary" in sent[1]


# ── T13: V2 tables created without touching V1 tables ────────────────────

def test_t13_v2_tables_isolated_from_v1():
    db = _db()
    conn = sqlite3.connect(str(db))
    v1_tables = [
        "hotlist_alerts", "hotlist_opportunities", "paper_trades", "strategy_results"
    ]
    for t in v1_tables:
        conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, data TEXT)")
    conn.commit()
    conn.close()

    strategy_repo = V2StrategyRepository(db)
    signal_repo = V2SignalRepository(db)
    order_repo = V2PaperOrderRepository(db)
    event_repo = V2OrderEventRepository(db)
    strategy_repo.ensure_table()
    signal_repo.ensure_table()
    order_repo.ensure_table()
    event_repo.ensure_table()

    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    for v1_t in v1_tables:
        assert v1_t in tables, f"V1 table {v1_t} should still exist"

    for v2_t in ("v2_strategies", "v2_signals", "v2_paper_orders", "v2_order_events"):
        assert v2_t in tables, f"V2 table {v2_t} should be created"

    conn2 = sqlite3.connect(str(db))
    for v1_t in v1_tables:
        count = conn2.execute(f"SELECT COUNT(*) FROM {v1_t}").fetchone()[0]
        assert count == 0, f"V1 table {v1_t} must not be modified"
    conn2.close()
