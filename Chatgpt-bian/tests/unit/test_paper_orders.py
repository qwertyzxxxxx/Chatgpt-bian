"""Unified paper portfolio tests — fill-check settlement, feeder, repository."""
from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from binance_ai_trader.paper.order_repository import PaperOrder, PaperOrderRepository
from binance_ai_trader.paper.settler import PaperOrderSettler
from binance_ai_trader.paper.feeder import PaperFeeder


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_order(
    direction="LONG",
    entry="100",
    stop_loss="95",
    tp1="105",
    tp2="110",
    status="OPEN",
    pushed=True,
    created_at=None,
    expires_at=None,
    order_id="test-order-1",
    source_id="src-1",
) -> PaperOrder:
    now = datetime.now(UTC)
    return PaperOrder(
        order_id=order_id,
        strategy_id="hotlist",
        source_type="hotlist",
        source_id=source_id,
        symbol="BTCUSDT",
        direction=direction,
        entry=Decimal(entry),
        stop_loss=Decimal(stop_loss),
        tp1=Decimal(tp1),
        tp2=Decimal(tp2),
        rr=Decimal("2"),
        status=status,
        result=None,
        pushed=pushed,
        alert_id=None,
        created_at=(created_at or now.isoformat(timespec="seconds")),
        filled_at=None,
        closed_at=None,
        expires_at=(expires_at or (now + timedelta(hours=24)).isoformat(timespec="seconds")),
        pnl_pct=None,
        rr_realized=None,
        duration_minutes=None,
        legacy=False,
    )


def _make_kline(low, high, close, offset_hours=1):
    """Make a mock Kline object."""
    k = MagicMock()
    k.low = Decimal(str(low))
    k.high = Decimal(str(high))
    k.close = Decimal(str(close))
    base_ms = int(datetime.now(UTC).timestamp() * 1000)
    k.close_time_ms = base_ms + offset_hours * 3600 * 1000
    return k


def _repo(tmp_path: Path) -> PaperOrderRepository:
    return PaperOrderRepository(tmp_path / "paper_test.db")


def _settler(repo: PaperOrderRepository) -> PaperOrderSettler:
    settler = PaperOrderSettler.__new__(PaperOrderSettler)
    settler._repo = repo
    settler._client = None
    return settler


# ── T1: LONG — entry NOT triggered → no TP ───────────────────────────────────

def test_long_entry_not_filled_no_tp(tmp_path):
    """If LONG candle.low never reaches entry, EXPIRED_NOT_FILLED — not TP."""
    repo = _repo(tmp_path)
    order = _make_order(direction="LONG", entry="100", stop_loss="95", tp1="105", tp2="110")
    repo.save(order)

    settler = _settler(repo)
    now_past = datetime.now(UTC) + timedelta(hours=25)
    candles = [_make_kline(low=101, high=115, close=115, offset_hours=i) for i in range(1, 5)]

    settler._settle_one = lambda o, now: settler._check_fill(o, candles, now_past, now_past - timedelta(hours=1))
    result = settler._check_fill(order, candles, now_past, now_past - timedelta(hours=1))
    assert result is True

    orders = repo.load_all()
    assert orders[0].result == "EXPIRED_NOT_FILLED"


# ── T2: SHORT — entry NOT triggered → no TP ──────────────────────────────────

def test_short_entry_not_filled_no_tp(tmp_path):
    """If SHORT candle.high never reaches entry, EXPIRED_NOT_FILLED — not TP."""
    repo = _repo(tmp_path)
    order = _make_order(
        direction="SHORT", entry="100", stop_loss="105",
        tp1="95", tp2="90", source_id="src-2", order_id="o2",
    )
    repo.save(order)

    settler = _settler(repo)
    now_past = datetime.now(UTC) + timedelta(hours=25)
    candles = [_make_kline(low=85, high=99, close=95, offset_hours=i) for i in range(1, 5)]

    result = settler._check_fill(order, candles, now_past, now_past - timedelta(hours=1))
    assert result is True

    orders = repo.load_all()
    assert orders[0].result == "EXPIRED_NOT_FILLED"


# ── T3: LONG — filled then TP1 hit ───────────────────────────────────────────

def test_long_filled_then_tp1(tmp_path):
    """LONG: candle.low touches entry, then candle.high hits TP1."""
    repo = _repo(tmp_path)
    order = _make_order(direction="LONG", entry="100", stop_loss="95", tp1="105", tp2="110")
    repo.save(order)

    settler = _settler(repo)
    now = datetime.now(UTC)
    expires = now + timedelta(hours=24)

    fill_candle = _make_kline(low=99, high=102, close=101, offset_hours=1)
    tp1_candle = _make_kline(low=101, high=106, close=105, offset_hours=2)
    candles = [fill_candle, tp1_candle]

    result = settler._check_fill(order, candles, now, expires)
    assert result is True

    orders = repo.load_all()
    assert orders[0].result == "TP1"


# ── T4: SHORT — filled then TP1 hit ──────────────────────────────────────────

def test_short_filled_then_tp1(tmp_path):
    """SHORT: candle.high touches entry, then candle.low hits TP1."""
    repo = _repo(tmp_path)
    order = _make_order(
        direction="SHORT", entry="100", stop_loss="105",
        tp1="95", tp2="90", source_id="src-4", order_id="o4",
    )
    repo.save(order)

    settler = _settler(repo)
    now = datetime.now(UTC)
    expires = now + timedelta(hours=24)

    fill_candle = _make_kline(low=98, high=101, close=99, offset_hours=1)
    tp1_candle = _make_kline(low=94, high=99, close=95, offset_hours=2)
    candles = [fill_candle, tp1_candle]

    result = settler._check_fill(order, candles, now, expires)
    assert result is True

    orders = repo.load_all()
    assert orders[0].result == "TP1"


# ── T5: EXPIRED_NOT_FILLED not in win-rate ────────────────────────────────────

def test_expired_not_filled_excluded_from_win_rate(tmp_path):
    """EXPIRED_NOT_FILLED orders must not be counted in win-rate denominator."""
    repo = _repo(tmp_path)

    tp1_order = _make_order(source_id="src-tp1", order_id="o-tp1")
    sl_order = _make_order(source_id="src-sl", order_id="o-sl")
    enf_order = _make_order(source_id="src-enf", order_id="o-enf")

    repo.save(tp1_order)
    repo.save(sl_order)
    repo.save(enf_order)

    now_str = datetime.now(UTC).isoformat(timespec="seconds")
    repo.update_settled("o-tp1", "TP1", now_str, Decimal("5"), Decimal("1"), 60)
    repo.update_settled("o-sl", "SL", now_str, Decimal("-3"), Decimal("-1"), 45)
    repo.update_settled("o-enf", "EXPIRED_NOT_FILLED", now_str, None, None, None)

    all_orders = repo.load_all()
    settled_decisive = [o for o in all_orders if o.result in ("TP1", "TP2", "SL")]
    wins = [o for o in settled_decisive if o.result in ("TP1", "TP2")]

    assert len(settled_decisive) == 2
    assert len(wins) == 1
    assert round(len(wins) / len(settled_decisive) * 100) == 50


# ── T6: pushed=True vs pushed=False ──────────────────────────────────────────

def test_pushed_vs_candidate_distinction(tmp_path):
    """pushed=True and pushed=False orders are independently queryable."""
    repo = _repo(tmp_path)
    pushed = _make_order(pushed=True, source_id="src-p", order_id="o-p")
    candidate = _make_order(pushed=False, source_id="src-c", order_id="o-c")
    repo.save(pushed)
    repo.save(candidate)

    pushed_orders = repo.load_all(pushed=True)
    candidate_orders = repo.load_all(pushed=False)

    assert len(pushed_orders) == 1
    assert pushed_orders[0].pushed is True
    assert len(candidate_orders) == 1
    assert candidate_orders[0].pushed is False


# ── T7: Telegram summary only uses pushed=True ───────────────────────────────

def test_telegram_summary_uses_only_pushed(tmp_path):
    """build_summary win rate only considers pushed=True orders."""
    from binance_ai_trader.paper.summary import build_summary

    repo = _repo(tmp_path)
    now = datetime.now(UTC)

    pushed_tp = _make_order(pushed=True, source_id="s1", order_id="o1")
    pushed_sl = _make_order(pushed=True, source_id="s2", order_id="o2")
    cand_sl = _make_order(pushed=False, source_id="s3", order_id="o3")

    repo.save(pushed_tp)
    repo.save(pushed_sl)
    repo.save(cand_sl)

    now_str = now.isoformat(timespec="seconds")
    repo.update_settled("o1", "TP1", now_str, Decimal("5"), Decimal("1"), 60)
    repo.update_settled("o2", "SL", now_str, Decimal("-3"), Decimal("-1"), 45)
    repo.update_settled("o3", "SL", now_str, Decimal("-3"), Decimal("-1"), 45)

    summary = build_summary(repo, window_hours=6)
    assert "胜率 50%" in summary
    assert "Hotlist 推送订单" in summary


# ── T8: Restart safety — OPEN/FILLED orders survive restart ──────────────────

def test_open_and_filled_orders_survive_restart(tmp_path):
    """Closing and reopening the repository must preserve OPEN and FILLED rows."""
    db_path = tmp_path / "paper_test.db"
    repo = PaperOrderRepository(db_path)
    order = _make_order()
    repo.save(order)
    repo.update_filled("test-order-1", datetime.now(UTC).isoformat(timespec="seconds"))
    repo.close()

    repo2 = PaperOrderRepository(db_path)
    open_orders = repo2.load_open()
    assert len(open_orders) == 1
    assert open_orders[0].status == "FILLED"
    repo2.close()


# ── T9: settle — OPEN with no fill stays OPEN (not expired) ──────────────────

def test_open_stays_open_if_not_expired_and_no_fill(tmp_path):
    """Orders that haven't expired and haven't been touched stay OPEN."""
    repo = _repo(tmp_path)
    order = _make_order(direction="LONG", entry="100")
    repo.save(order)

    settler = _settler(repo)
    now = datetime.now(UTC)
    expires = now + timedelta(hours=24)
    candles = [_make_kline(low=101, high=110, close=105, offset_hours=i) for i in range(1, 5)]

    result = settler._check_fill(order, candles, now, expires)
    assert result is False

    orders = repo.load_all()
    assert orders[0].status == "OPEN"
    assert orders[0].result is None


# ── T10: LONG TP2 after fill ──────────────────────────────────────────────────

def test_long_tp2_after_fill(tmp_path):
    """LONG: entry filled, then TP2 reached → result=TP2."""
    repo = _repo(tmp_path)
    order = _make_order(direction="LONG", entry="100", stop_loss="95", tp1="105", tp2="110")
    repo.save(order)

    settler = _settler(repo)
    now = datetime.now(UTC)
    expires = now + timedelta(hours=24)

    candles = [
        _make_kline(low=99, high=103, close=102, offset_hours=1),
        _make_kline(low=103, high=112, close=111, offset_hours=2),
    ]
    settler._check_fill(order, candles, now, expires)

    orders = repo.load_all()
    assert orders[0].result == "TP2"


# ── T11: SHORT SL after fill ──────────────────────────────────────────────────

def test_short_sl_after_fill(tmp_path):
    """SHORT: entry filled, then SL triggered → result=SL."""
    repo = _repo(tmp_path)
    order = _make_order(
        direction="SHORT", entry="100", stop_loss="105",
        tp1="95", tp2="90", source_id="src-11", order_id="o11",
    )
    repo.save(order)

    settler = _settler(repo)
    now = datetime.now(UTC)
    expires = now + timedelta(hours=24)

    candles = [
        _make_kline(low=98, high=101, close=99, offset_hours=1),
        _make_kline(low=99, high=106, close=105, offset_hours=2),
    ]
    settler._check_fill(order, candles, now, expires)

    orders = repo.load_all()
    assert orders[0].result == "SL"


# ── T12: Dashboard can see all paper_orders ───────────────────────────────────

def test_dashboard_sees_all_paper_orders(tmp_path):
    """load_all with no filters returns all orders regardless of pushed/strategy."""
    repo = _repo(tmp_path)
    orders = [
        _make_order(pushed=True, source_id=f"s{i}", order_id=f"o{i}",
                    strategy_id="hotlist" if i < 3 else "baseline_v1")
        for i in range(5)
    ]
    for o in orders:
        repo.save(o)

    all_orders = repo.load_all(limit=100)
    assert len(all_orders) == 5

    hotlist_orders = repo.load_all(strategy_id="hotlist", limit=100)
    assert len(hotlist_orders) == 3

    baseline_orders = repo.load_all(strategy_id="baseline_v1", limit=100)
    assert len(baseline_orders) == 2


# ── Feeder tests ──────────────────────────────────────────────────────────────

def _setup_feeder_db(tmp_path: Path) -> Path:
    """Create a minimal DB with hotlist_alerts and hotlist_opportunities."""
    db = tmp_path / "market_data.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE hotlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, direction TEXT, entry TEXT,
            stop_loss TEXT, tp1 TEXT, tp2 TEXT, rr TEXT,
            created_at TEXT, expires_at TEXT, rank_type TEXT DEFAULT 'UNKNOWN'
        )
    """)
    con.execute("""
        CREATE TABLE hotlist_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, direction TEXT, entry TEXT,
            sl TEXT, tp1 TEXT, tp2 TEXT, rr TEXT,
            created_at TEXT, expires_at TEXT
        )
    """)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    exp = (datetime.now(UTC) + timedelta(hours=24)).isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO hotlist_alerts (symbol,direction,entry,stop_loss,tp1,tp2,rr,created_at,expires_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("BTCUSDT", "LONG", "100", "95", "105", "110", "2.0", now, exp),
    )
    con.execute(
        "INSERT INTO hotlist_opportunities (symbol,direction,entry,sl,tp1,tp2,rr,created_at,expires_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("ETHUSDT", "SHORT", "200", "210", "190", "180", "2.0", now, exp),
    )
    con.commit()
    con.close()
    return db


def test_feeder_creates_pushed_and_candidate(tmp_path):
    """Feeder creates pushed=True from hotlist_alerts and pushed=False from opportunities."""
    db = _setup_feeder_db(tmp_path)
    repo = _repo(tmp_path)
    feeder = PaperFeeder(repo, db)
    result = feeder.feed_all()

    assert result["hotlist_pushed"] == 1
    assert result["hotlist_candidate"] == 1

    pushed = repo.load_all(pushed=True)
    candidate = repo.load_all(pushed=False)
    assert len(pushed) == 1 and pushed[0].symbol == "BTCUSDT"
    assert len(candidate) == 1 and candidate[0].symbol == "ETHUSDT"


def test_feeder_deduplication(tmp_path):
    """Feeder must not create duplicate paper_orders for the same source_id."""
    db = _setup_feeder_db(tmp_path)
    repo = _repo(tmp_path)
    feeder = PaperFeeder(repo, db)

    r1 = feeder.feed_all()
    r2 = feeder.feed_all()

    assert r1["hotlist_pushed"] == 1
    assert r2["hotlist_pushed"] == 0
    assert len(repo.load_all(limit=200)) == 2
