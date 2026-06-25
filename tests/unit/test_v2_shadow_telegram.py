"""Unit tests — V2 Shadow Telegram (health tracker, startup, shadow report,
health check, alerts).

T14  HealthTracker: scan_ok resets api_consecutive_failures
T15  HealthTracker: scan_overdue returns True after threshold
T16  HealthTracker: errors_last_n_hours only returns recent entries
T17  AlertSender: maybe_alert_api fires at threshold, not before
T18  AlertSender: maybe_alert_settle fires at threshold, not before
T19  AlertSender: alert_db_failure / alert_scan_overdue send [V2] ALERT prefix
T20  Startup: message contains [V2] Started + all required fields
T21  Startup: SHA/branch/PID/DB all appear in message
T22  ShadowReport: [V2] Hotlist Paper prefix + all 4 sections present
T23  ShadowReport: filled orders appear in 持仓, open in 挂单
T24  ShadowReport: recently settled orders appear in 最近结算
T25  HealthCheck: all-OK path produces short summary
T26  HealthCheck: FAIL path shows issues for stale scan
T27  build_v2_tasks: startup + scan + settle always created
T28  build_v2_tasks: shadow_report task absent when disabled
T29  build_v2_tasks: health_check task absent when disabled
"""
from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

def _db() -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return Path(f.name)


def _captured_notifier():
    msgs: list[str] = []
    notifier = MagicMock()
    notifier.send.side_effect = msgs.append
    return notifier, msgs


def _order_repo(db: Path):
    from binance_ai_trader.v2.paper_portfolio.repository import V2PaperOrderRepository
    repo = V2PaperOrderRepository(db)
    repo.ensure_table()
    return repo


def _perf_calc(order_repo):
    from binance_ai_trader.v2.performance.calculator import V2PerformanceCalculator
    return V2PerformanceCalculator(order_repo)


def _make_order(db: Path, status: str, result: str | None = None,
                filled_at: str | None = None, closed_at: str | None = None,
                pnl_pct: str | None = None, duration_minutes: int | None = None):
    from binance_ai_trader.v2.paper_portfolio.repository import (
        V2PaperOrder, V2PaperOrderRepository, make_order_id,
    )
    from binance_ai_trader.v2.signals.repository import V2Signal, V2SignalRepository, make_signal_id
    sig_repo = V2SignalRepository(db)
    sig_repo.ensure_table()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    sig = V2Signal(
        signal_id=make_signal_id(), strategy_id="hotlist_momentum_v2",
        symbol="BTCUSDT", direction="LONG",
        entry=Decimal("100"), stop_loss=Decimal("95"),
        tp1=Decimal("110"), tp2=Decimal("120"), rr=Decimal("2"),
        reason="test", metadata_json="{}", created_at=now,
    )
    sig_repo.save(sig)
    repo = V2PaperOrderRepository(db)
    repo.ensure_table()
    o = V2PaperOrder(
        order_id=make_order_id(), signal_id=sig.signal_id,
        strategy_id="hotlist_momentum_v2", symbol="BTCUSDT", direction="LONG",
        entry=Decimal("100"), stop_loss=Decimal("95"),
        tp1=Decimal("110"), tp2=Decimal("120"), rr=Decimal("2"),
        status=status, result=result,
        created_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds"),
        filled_at=filled_at, closed_at=closed_at,
        expires_at=(datetime.now(UTC) + timedelta(hours=22)).isoformat(timespec="seconds"),
        pnl_pct=Decimal(pnl_pct) if pnl_pct else None,
        rr_realized=Decimal("2.0") if pnl_pct else None,
        duration_minutes=duration_minutes,
        pushed=True, metadata_json="{}",
    )
    repo.save(o)
    return o, repo


# ─────────────────────────────────────────────────────────────────────────────
# T14 HealthTracker: scan_ok resets api_consecutive_failures
# ─────────────────────────────────────────────────────────────────────────────
def test_t14_health_tracker_scan_ok_resets_failures():
    from binance_ai_trader.v2.monitoring.health_tracker import V2HealthTracker
    t = V2HealthTracker()
    t.record_api_failure("err1")
    t.record_api_failure("err2")
    assert t.api_consecutive_failures == 2
    t.record_scan_ok()
    assert t.api_consecutive_failures == 0
    assert t.last_scan_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# T15 HealthTracker: scan_overdue returns True after threshold
# ─────────────────────────────────────────────────────────────────────────────
def test_t15_health_tracker_scan_overdue():
    from binance_ai_trader.v2.monitoring.health_tracker import V2HealthTracker
    t = V2HealthTracker()
    t.last_scan_at = datetime.now(UTC) - timedelta(hours=3)
    assert t.scan_overdue(hours=2.0) is True
    t.last_scan_at = datetime.now(UTC) - timedelta(minutes=10)
    assert t.scan_overdue(hours=2.0) is False


# ─────────────────────────────────────────────────────────────────────────────
# T16 HealthTracker: errors_last_n_hours only returns recent entries
# ─────────────────────────────────────────────────────────────────────────────
def test_t16_health_tracker_errors_window():
    from binance_ai_trader.v2.monitoring.health_tracker import V2HealthTracker
    t = V2HealthTracker()
    t.record_error("scan", "old error")
    t._errors[-1] = (datetime.now(UTC) - timedelta(hours=8), "scan", "old error")
    t.record_error("scan", "new error")
    recent = t.errors_last_n_hours(6)
    assert len(recent) == 1
    assert recent[0][2] == "new error"


# ─────────────────────────────────────────────────────────────────────────────
# T17 AlertSender: maybe_alert_api fires at threshold, not before
# ─────────────────────────────────────────────────────────────────────────────
def test_t17_alert_api_threshold():
    from binance_ai_trader.v2.telegram.alerts import V2AlertSender
    notifier, msgs = _captured_notifier()
    a = V2AlertSender(notifier)
    assert a.maybe_alert_api(2) is False
    assert len(msgs) == 0
    assert a.maybe_alert_api(3) is True
    assert len(msgs) == 1
    assert msgs[0].startswith("[V2] ALERT")
    assert "API" in msgs[0]


# ─────────────────────────────────────────────────────────────────────────────
# T18 AlertSender: maybe_alert_settle fires at threshold, not before
# ─────────────────────────────────────────────────────────────────────────────
def test_t18_alert_settle_threshold():
    from binance_ai_trader.v2.telegram.alerts import V2AlertSender
    notifier, msgs = _captured_notifier()
    a = V2AlertSender(notifier)
    assert a.maybe_alert_settle(2) is False
    assert a.maybe_alert_settle(3) is True
    assert msgs[0].startswith("[V2] ALERT")
    assert "Settlement" in msgs[0]


# ─────────────────────────────────────────────────────────────────────────────
# T19 AlertSender: alert_db_failure / alert_scan_overdue send [V2] ALERT prefix
# ─────────────────────────────────────────────────────────────────────────────
def test_t19_alert_prefix():
    from binance_ai_trader.v2.telegram.alerts import V2AlertSender
    notifier, msgs = _captured_notifier()
    a = V2AlertSender(notifier)
    a.alert_db_failure("write error")
    a.alert_scan_overdue(2.5)
    assert all(m.startswith("[V2] ALERT") for m in msgs)
    assert any("DB" in m for m in msgs)
    assert any("扫描" in m for m in msgs)


# ─────────────────────────────────────────────────────────────────────────────
# T20 Startup: message contains [V2] Started + all required fields
# ─────────────────────────────────────────────────────────────────────────────
def test_t20_startup_message_fields():
    from binance_ai_trader.v2.telegram.startup import send_v2_startup
    notifier, msgs = _captured_notifier()
    send_v2_startup(
        notifier,
        db_path=Path("data/test.db"),
        shadow_report_enabled=True,
        health_check_enabled=True,
        report_interval_hours=1,
        health_interval_hours=6,
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.startswith("[V2] Started")
    for field in ("版本", "Git SHA", "Branch", "PID", "DB", "V2 Hotlist", "Shadow Report", "Health Check"):
        assert field in msg, f"missing field: {field}"


# ─────────────────────────────────────────────────────────────────────────────
# T21 Startup: Shadow/Health ON/OFF flags reflected
# ─────────────────────────────────────────────────────────────────────────────
def test_t21_startup_on_off_flags():
    from binance_ai_trader.v2.telegram.startup import send_v2_startup
    notifier, msgs = _captured_notifier()
    send_v2_startup(notifier, Path("db"), shadow_report_enabled=False, health_check_enabled=False)
    msg = msgs[0]
    assert "Shadow Report: OFF" in msg
    assert "Health Check:  OFF" in msg

    msgs.clear()
    send_v2_startup(notifier, Path("db"), shadow_report_enabled=True, health_check_enabled=True,
                    report_interval_hours=2, health_interval_hours=12)
    msg2 = msgs[0]
    assert "ON  (2h)" in msg2
    assert "ON  (12h)" in msg2


# ─────────────────────────────────────────────────────────────────────────────
# T22 ShadowReport: [V2] Hotlist Paper prefix + all 4 sections
# ─────────────────────────────────────────────────────────────────────────────
def test_t22_shadow_report_sections():
    from binance_ai_trader.v2.telegram.shadow_report import V2ShadowReporter
    db = _db()
    order_repo = _order_repo(db)
    perf_calc = _perf_calc(order_repo)
    notifier, msgs = _captured_notifier()
    reporter = V2ShadowReporter(notifier, order_repo, perf_calc, "hotlist_momentum_v2")
    reporter.send_report()
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.startswith("[V2] Hotlist Paper")
    for section in ("🏆 总绩效", "📂 当前持仓", "📋 当前挂单", "✅ 最近结算"):
        assert section in msg, f"missing section: {section}"


# ─────────────────────────────────────────────────────────────────────────────
# T23 ShadowReport: FILLED orders in 持仓, OPEN orders in 挂单
# ─────────────────────────────────────────────────────────────────────────────
def test_t23_shadow_report_positions_vs_pending():
    from binance_ai_trader.v2.telegram.shadow_report import V2ShadowReporter
    db = _db()
    now = datetime.now(UTC)
    _make_order(db, status="FILLED", filled_at=now.isoformat(timespec="seconds"))
    _make_order(db, status="OPEN")
    order_repo = _order_repo(db)
    perf_calc = _perf_calc(order_repo)
    notifier, msgs = _captured_notifier()
    reporter = V2ShadowReporter(notifier, order_repo, perf_calc, "hotlist_momentum_v2")
    reporter.send_report()
    msg = msgs[0]
    pos_idx  = msg.index("📂 当前持仓")
    pend_idx = msg.index("📋 当前挂单")
    settl_idx = msg.index("✅ 最近结算")
    pos_section  = msg[pos_idx:pend_idx]
    pend_section = msg[pend_idx:settl_idx]
    assert "共 1 笔" in pos_section,  "filled order not in 持仓"
    assert "共 1 笔" in pend_section, "open order not in 挂单"


# ─────────────────────────────────────────────────────────────────────────────
# T24 ShadowReport: recently settled orders appear in 最近结算
# ─────────────────────────────────────────────────────────────────────────────
def test_t24_shadow_report_recent_settled():
    from binance_ai_trader.v2.telegram.shadow_report import V2ShadowReporter
    db = _db()
    now = datetime.now(UTC)
    _make_order(
        db, status="TP1", result="TP1",
        filled_at=(now - timedelta(hours=1)).isoformat(timespec="seconds"),
        closed_at=now.isoformat(timespec="seconds"),
        pnl_pct="5.2", duration_minutes=65,
    )
    order_repo = _order_repo(db)
    perf_calc = _perf_calc(order_repo)
    notifier, msgs = _captured_notifier()
    reporter = V2ShadowReporter(notifier, order_repo, perf_calc, "hotlist_momentum_v2")
    reporter.send_report()
    msg = msgs[0]
    settl_idx = msg.index("✅ 最近结算")
    settl_section = msg[settl_idx:]
    assert "TP1" in settl_section
    assert "+5.20%" in settl_section
    assert "1h05m" in settl_section


# ─────────────────────────────────────────────────────────────────────────────
# T25 HealthCheck: all-OK path produces short summary
# ─────────────────────────────────────────────────────────────────────────────
def test_t25_health_check_all_ok():
    from binance_ai_trader.v2.monitoring.health_tracker import V2HealthTracker
    from binance_ai_trader.v2.telegram.health_check import V2HealthReporter
    tracker = V2HealthTracker()
    now = datetime.now(UTC)
    tracker.last_scan_at   = now
    tracker.last_settle_at = now
    tracker.last_report_at = now
    notifier, msgs = _captured_notifier()
    reporter = V2HealthReporter(notifier, tracker)
    reporter.send_health_check()
    msg = msgs[0]
    assert msg.startswith("[V2] Health Check")
    assert "All OK" in msg
    assert "FAIL" not in msg


# ─────────────────────────────────────────────────────────────────────────────
# T26 HealthCheck: FAIL path shows issues for stale scan
# ─────────────────────────────────────────────────────────────────────────────
def test_t26_health_check_stale_scan():
    from binance_ai_trader.v2.monitoring.health_tracker import V2HealthTracker
    from binance_ai_trader.v2.telegram.health_check import V2HealthReporter
    tracker = V2HealthTracker()
    now = datetime.now(UTC)
    tracker.last_scan_at   = now - timedelta(hours=2)
    tracker.last_settle_at = now
    tracker.last_report_at = now
    notifier, msgs = _captured_notifier()
    reporter = V2HealthReporter(notifier, tracker)
    reporter.send_health_check()
    msg = msgs[0]
    assert "FAIL" in msg
    assert "Scan" in msg
    assert "All OK" not in msg


# ─────────────────────────────────────────────────────────────────────────────
# T27 build_v2_tasks: startup + scan + settle always created
# ─────────────────────────────────────────────────────────────────────────────
def test_t27_build_v2_tasks_core_always_present():
    from binance_ai_trader.v2.runner.tasks import build_v2_tasks
    from binance_ai_trader.config import UniverseConfig
    db = _db()
    universe = MagicMock(spec=UniverseConfig)
    client_mock = MagicMock()
    with patch("binance_ai_trader.v2.runner.tasks.BinancePublicClient", return_value=client_mock):
        tasks = build_v2_tasks(
            db_path=db,
            universe_config=universe,
            telegram=None,
            shadow_report_enabled=False,
            health_check_enabled=False,
        )
    names = {t.event_type for t in tasks}
    assert "v2_startup"      in names
    assert "v2_hotlist_scan" in names
    assert "v2_paper_settle" in names


# ─────────────────────────────────────────────────────────────────────────────
# T28 build_v2_tasks: shadow_report absent when disabled or no telegram
# ─────────────────────────────────────────────────────────────────────────────
def test_t28_build_v2_tasks_shadow_report_disabled():
    from binance_ai_trader.v2.runner.tasks import build_v2_tasks
    from binance_ai_trader.config import UniverseConfig
    db = _db()
    universe = MagicMock(spec=UniverseConfig)
    client_mock = MagicMock()
    notifier, _ = _captured_notifier()
    with patch("binance_ai_trader.v2.runner.tasks.BinancePublicClient", return_value=client_mock):
        tasks_off = build_v2_tasks(
            db_path=db, universe_config=universe,
            telegram=notifier, shadow_report_enabled=False, health_check_enabled=False,
        )
        tasks_no_tg = build_v2_tasks(
            db_path=db, universe_config=universe,
            telegram=None, shadow_report_enabled=True, health_check_enabled=False,
        )
    for tasks in (tasks_off, tasks_no_tg):
        assert "v2_shadow_report" not in {t.event_type for t in tasks}


# ─────────────────────────────────────────────────────────────────────────────
# T29 build_v2_tasks: health_check absent when disabled or no telegram
# ─────────────────────────────────────────────────────────────────────────────
def test_t29_build_v2_tasks_health_check_disabled():
    from binance_ai_trader.v2.runner.tasks import build_v2_tasks
    from binance_ai_trader.config import UniverseConfig
    db = _db()
    universe = MagicMock(spec=UniverseConfig)
    client_mock = MagicMock()
    notifier, _ = _captured_notifier()
    with patch("binance_ai_trader.v2.runner.tasks.BinancePublicClient", return_value=client_mock):
        tasks_off = build_v2_tasks(
            db_path=db, universe_config=universe,
            telegram=notifier, shadow_report_enabled=False, health_check_enabled=False,
        )
        tasks_no_tg = build_v2_tasks(
            db_path=db, universe_config=universe,
            telegram=None, shadow_report_enabled=False, health_check_enabled=True,
        )
    for tasks in (tasks_off, tasks_no_tg):
        assert "v2_health_check" not in {t.event_type for t in tasks}
