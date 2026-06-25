"""V2 runner task factory — builds RunnerTask objects for the V2 pipeline.

Tasks (returned by build_v2_tasks):
  v2_startup        — once at boot: [V2] Started
  v2_hotlist_scan   — every 15min: scan → signal → risk → paper order
  v2_paper_settle   — every 15min: settle open V2 orders + write order_events
  v2_shadow_report  — every 1h:    [V2] Hotlist Paper (perf + positions + recent)
  v2_health_check   — every 6h:    [V2] Health Check  (default ON, can be disabled)

Immediate alerts fired inline (not as scheduled tasks):
  [V2] ALERT  — API/settle/DB failures, scan overdue
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.runner.engine import RunnerTask, RunnerTaskResult
from binance_ai_trader.v2.monitoring.health_tracker import V2HealthTracker
from binance_ai_trader.v2.order_events.repository import V2OrderEvent, V2OrderEventRepository, make_event_id
from binance_ai_trader.v2.paper_portfolio.repository import V2PaperOrder, V2PaperOrderRepository, make_order_id
from binance_ai_trader.v2.performance.calculator import V2PerformanceCalculator
from binance_ai_trader.v2.risk.engine import V2RiskEngine
from binance_ai_trader.v2.settlement.settler import V2Settler
from binance_ai_trader.v2.signals.repository import V2SignalRepository
from binance_ai_trader.v2.strategies.hotlist_momentum import HotlistMomentumV2
from binance_ai_trader.v2.strategy_registry.repository import (
    V2StrategyRepository,
    register_hotlist_momentum_v2,
)
from binance_ai_trader.v2.telegram.alerts import V2AlertSender
from binance_ai_trader.v2.telegram.health_check import V2HealthReporter
from binance_ai_trader.v2.telegram.notifier import V2TelegramNotifier
from binance_ai_trader.v2.telegram.shadow_report import V2ShadowReporter
from binance_ai_trader.v2.telegram.startup import send_v2_startup

log = logging.getLogger(__name__)

_STRATEGY_ID = "hotlist_momentum_v2"
_SCAN_OVERDUE_HOURS = 2.0


def build_v2_tasks(
    db_path: Path,
    universe_config: UniverseConfig,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    report_interval: timedelta = timedelta(hours=1),
    health_interval: timedelta = timedelta(hours=6),
    shadow_report_enabled: bool = True,
    health_check_enabled: bool = True,
) -> tuple[RunnerTask, ...]:
    """Bootstrap V2 tables, register strategy, return runner tasks."""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    strategy_repo = V2StrategyRepository(db_path)
    signal_repo   = V2SignalRepository(db_path)
    order_repo    = V2PaperOrderRepository(db_path)
    event_repo    = V2OrderEventRepository(db_path)

    strategy_repo.ensure_table()
    signal_repo.ensure_table()
    order_repo.ensure_table()
    event_repo.ensure_table()

    strategy    = register_hotlist_momentum_v2(db_path)
    risk_engine = V2RiskEngine(order_repo)
    hotlist_v2  = HotlistMomentumV2(client, universe_config, signal_repo, strategy)
    settler     = V2Settler(order_repo, event_repo, client)
    perf_calc   = V2PerformanceCalculator(order_repo)
    tracker     = V2HealthTracker()

    v2_tg    = V2TelegramNotifier(telegram) if telegram else None
    alerter  = V2AlertSender(telegram) if telegram else None
    reporter = (
        V2ShadowReporter(telegram, order_repo, perf_calc, _STRATEGY_ID)
        if (telegram and shadow_report_enabled) else None
    )
    health_reporter = (
        V2HealthReporter(telegram, tracker)
        if (telegram and health_check_enabled) else None
    )

    def _startup_task() -> RunnerTaskResult:
        if telegram:
            send_v2_startup(
                telegram,
                db_path=db_path,
                shadow_report_enabled=shadow_report_enabled,
                health_check_enabled=health_check_enabled,
                report_interval_hours=int(report_interval.total_seconds() // 3600),
                health_interval_hours=int(health_interval.total_seconds() // 3600),
            )
        return RunnerTaskResult("SUCCEEDED", {"startup": "sent"})

    def _scan_task() -> RunnerTaskResult:
        current_strategy = strategy_repo.get(_STRATEGY_ID)
        if current_strategy is None or not current_strategy.enabled:
            return RunnerTaskResult("SKIPPED", {"reason": "strategy disabled"})

        try:
            new_signals = hotlist_v2.scan()
            tracker.record_scan_ok()
        except Exception as exc:
            tracker.record_api_failure(str(exc))
            if alerter:
                alerter.maybe_alert_api(tracker.api_consecutive_failures)
            raise

        orders_created = 0
        now = datetime.now(UTC)

        for signal in new_signals:
            decision = risk_engine.check(signal, current_strategy)
            if not decision.allowed:
                log.info(
                    "[V2] risk rejected %s %s: %s",
                    signal.symbol, signal.direction, decision.reason,
                )
                continue

            expires_at = now + timedelta(hours=current_strategy.max_hold_hours)
            order = V2PaperOrder(
                order_id=make_order_id(),
                signal_id=signal.signal_id,
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                direction=signal.direction,
                entry=signal.entry,
                stop_loss=signal.stop_loss,
                tp1=signal.tp1,
                tp2=signal.tp2,
                rr=signal.rr,
                status="OPEN",
                result=None,
                created_at=now.isoformat(timespec="seconds"),
                filled_at=None,
                closed_at=None,
                expires_at=expires_at.isoformat(timespec="seconds"),
                pnl_pct=None,
                rr_realized=None,
                duration_minutes=None,
                pushed=True,
                metadata_json=signal.metadata_json,
            )
            try:
                order_repo.save(order)
            except Exception as exc:
                tracker.record_db_failure(str(exc))
                if alerter:
                    alerter.alert_db_failure(str(exc))
                raise

            event_repo.append(V2OrderEvent(
                event_id=make_event_id(),
                order_id=order.order_id,
                event_type="CREATED",
                old_status=None,
                new_status="OPEN",
                candle_high=None,
                candle_low=None,
                triggered_at=now.isoformat(timespec="seconds"),
                metadata_json="{}",
            ))
            orders_created += 1
            log.info("[V2] paper order created: %s %s", order.symbol, order.direction)

            if v2_tg:
                v2_tg.send_signal(signal)

        if alerter and tracker.scan_overdue(_SCAN_OVERDUE_HOURS):
            alerter.alert_scan_overdue(_SCAN_OVERDUE_HOURS)

        return RunnerTaskResult("SUCCEEDED", {
            "signals": len(new_signals),
            "orders_created": orders_created,
        })

    def _settle_task() -> RunnerTaskResult:
        try:
            updated = settler.settle_all()
            tracker.record_settle_ok()
            return RunnerTaskResult("SUCCEEDED", {"settled": updated})
        except Exception as exc:
            tracker.record_settle_failure(str(exc))
            if alerter:
                alerter.maybe_alert_settle(tracker.settle_consecutive_failures)
            raise

    def _shadow_report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
            tracker.record_report_ok()
        return RunnerTaskResult("SUCCEEDED", {})

    def _health_check_task() -> RunnerTaskResult:
        if health_reporter:
            health_reporter.send_health_check()
        return RunnerTaskResult("SUCCEEDED", {})

    tasks: list[RunnerTask] = [
        RunnerTask("v2_startup",      _startup_task,       interval=timedelta(days=36500), startup_immediate=True),
        RunnerTask("v2_hotlist_scan", _scan_task,          interval=scan_interval,          startup_immediate=True),
        RunnerTask("v2_paper_settle", _settle_task,        interval=settle_interval,         startup_immediate=True),
    ]

    if reporter:
        tasks.append(RunnerTask("v2_shadow_report", _shadow_report_task, interval=report_interval))

    if health_reporter:
        tasks.append(RunnerTask("v2_health_check", _health_check_task, interval=health_interval))

    return tuple(tasks)
