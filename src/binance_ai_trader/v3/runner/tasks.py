"""V3 runner task factory — builds RunnerTask objects for the V3 pipeline.

Tasks (returned by build_v3_tasks):
  v3_hotlist_scan    — every 15min: generate candidates → pipeline → paper orders
  v3_paper_settle    — every 15min: settle open V3 orders
  v3_shadow_report   — every 1h:   📊 V3 Paper Portfolio
  v3_weekly_review   — every 7d:   📋 Weekly Strategy Review
  v3_health_check    — every 6h:   health ping

All permanent data stored in PostgreSQL via PG repositories.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.runner.engine import RunnerTask, RunnerTaskResult
from binance_ai_trader.v3.candidates.repository import V3CandidateRepository
from binance_ai_trader.v3.paper.repository import (
    V3OrderEvent,
    V3PaperOrder,
    V3PaperOrderRepository,
    make_event_id,
    make_order_id,
)
from binance_ai_trader.v3.performance.calculator import V3PerformanceCalculator
from binance_ai_trader.v3.pipeline import V3Pipeline
from binance_ai_trader.v3.push_queue.repository import V3PushQueueRepository
from binance_ai_trader.v3.risk.engine import RiskConfig
from binance_ai_trader.v3.live.engine import LiveMirrorEngine
from binance_ai_trader.v3.live.reporter import LiveHourlyReporter
from binance_ai_trader.v3.settlement.settler import V3Settler
from binance_ai_trader.v3.strategies.hotlist import HotlistStrategyV3
from binance_ai_trader.v3.telegram.notifier import V3TelegramNotifier
from binance_ai_trader.v3.telegram.shadow_report import V3ShadowReporter
from binance_ai_trader.v3.telegram.weekly_review import send_weekly_review

log = logging.getLogger(__name__)

_STRATEGY_ID = "hotlist_momentum_v3"
_HOLD_HOURS  = 24


def build_v3_tasks(
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
    weekly_review_interval: timedelta = timedelta(days=7),
    shadow_report_enabled: bool = True,
    health_check_enabled: bool = True,
    dedup_hours: int = 24,
    max_open_orders: int = 5,
    live_mirror: LiveMirrorEngine | None = None,
    live_sync_interval: timedelta = timedelta(minutes=15),
    live_report_interval: timedelta = timedelta(hours=1),
) -> tuple[RunnerTask, ...]:
    """Bootstrap V3 repos (PostgreSQL) and return runner tasks."""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    cand_repo  = V3CandidateRepository()
    push_repo  = V3PushQueueRepository()
    order_repo = V3PaperOrderRepository()
    perf_calc  = V3PerformanceCalculator(order_repo)

    strategy   = HotlistStrategyV3(client, universe_config)
    risk_cfg   = RiskConfig(strategy_id=_STRATEGY_ID, max_open_orders=max_open_orders)
    pipeline   = V3Pipeline(db_path, dedup_hours=dedup_hours, risk_config=risk_cfg)
    settler    = V3Settler(order_repo, client, notifier=telegram)

    v3_tg    = V3TelegramNotifier(telegram) if telegram else None
    live_reporter = (
        LiveHourlyReporter(live_mirror, live_mirror._repo, telegram)
        if live_mirror and telegram else None
    )
    reporter = (
        V3ShadowReporter(
            telegram, order_repo, perf_calc, _STRATEGY_ID,
            client=client,
            scan_interval_minutes=int(scan_interval.total_seconds() // 60),
            settle_interval_minutes=int(settle_interval.total_seconds() // 60),
            summary_interval_hours=int(report_interval.total_seconds() // 3600),
        )
        if (telegram and shadow_report_enabled) else None
    )

    def _scan_task() -> RunnerTaskResult:
        now = datetime.now(UTC)
        result = pipeline.run(strategy, now=now)

        orders_created = 0
        for candidate in result.candidates:
            if order_repo.exists_open_for_symbol_direction(
                _STRATEGY_ID, candidate.symbol, candidate.direction
            ):
                continue

            expires_at = (now + timedelta(hours=_HOLD_HOURS)).isoformat(timespec="seconds")
            order = V3PaperOrder(
                order_id=make_order_id(),
                signal_id=candidate.signal_id,
                strategy_id=_STRATEGY_ID,
                symbol=candidate.symbol,
                direction=candidate.direction,
                entry=Decimal(candidate.entry),
                stop_loss=Decimal(candidate.sl),
                tp1=Decimal(candidate.tp1),
                tp2=Decimal(candidate.tp2) if candidate.tp2 else Decimal(candidate.tp1),
                rr=Decimal(candidate.rr),
                status="OPEN",
                result=None,
                created_at=now.isoformat(timespec="seconds"),
                filled_at=None,
                closed_at=None,
                expires_at=expires_at,
                pnl_pct=None,
                rr_realized=None,
                pushed=True,
                metadata_json="{}",
            )
            order_repo.save(order)
            order_repo.append_event(V3OrderEvent(
                event_id=make_event_id(),
                order_id=order.order_id,
                signal_id=candidate.signal_id,
                event_type="CREATED",
                old_status=None,
                new_status="OPEN",
                candle_high=None,
                candle_low=None,
                triggered_at=now.isoformat(timespec="seconds"),
                metadata_json="{}",
            ))
            orders_created += 1

            live_prefix: str | None = None
            if live_mirror and live_mirror.is_enabled():
                try:
                    live_result = live_mirror.try_place(candidate)
                    live_prefix = live_result.prefix()
                except Exception:
                    log.exception("[V3] live mirror try_place failed for %s", candidate.signal_id)
                    live_prefix = "【实盘未下单：内部错误】"

            if v3_tg:
                v3_tg.send_candidate(candidate, hold_hours=_HOLD_HOURS, live_prefix=live_prefix)

            push_items = push_repo.load_by_signal(candidate.signal_id)
            if push_items:
                push_repo.mark_sent(push_id=push_items[0].push_id)

        log.info(
            "[V3] scan done — pushed=%d orders_created=%d blocked=%d",
            result.pushed, orders_created, result.total_blocked,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":     "v3_hotlist_scan",
            "scanned":        result.scanned,
            "pushed":         result.pushed,
            "orders_created": orders_created,
            "blocked_risk":   result.blocked_risk,
            "blocked_dedup":  result.blocked_dedup,
        })

    def _settle_task() -> RunnerTaskResult:
        updated = settler.settle_all()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v3_paper_settle", "settled": updated})

    def _live_sync_task() -> RunnerTaskResult:
        if live_mirror and live_mirror.is_enabled():
            updated = live_mirror.sync_all()
            return RunnerTaskResult("SUCCEEDED", {"event_type": "v3_live_sync", "updated": updated})
        return RunnerTaskResult("SKIPPED")

    def _live_report_task() -> RunnerTaskResult:
        if live_mirror and live_mirror.is_enabled() and live_reporter:
            live_reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v3_live_report"})

    def _report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v3_shadow_report"})

    def _weekly_review_task() -> RunnerTaskResult:
        if telegram:
            send_weekly_review(telegram, order_repo, perf_calc, _STRATEGY_ID)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v3_weekly_review"})

    def _health_task() -> RunnerTaskResult:
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v3_health_check"})

    tasks: list[RunnerTask] = [
        RunnerTask("v3_hotlist_scan",  _scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("v3_paper_settle",  _settle_task, interval=settle_interval, startup_immediate=True),
    ]
    if live_mirror:
        tasks.append(RunnerTask("v3_live_sync",   _live_sync_task,   interval=live_sync_interval,   startup_immediate=True))
        tasks.append(RunnerTask("v3_live_report", _live_report_task, interval=live_report_interval))
    if reporter:
        tasks.append(RunnerTask("v3_shadow_report", _report_task, interval=report_interval, startup_immediate=True))
    tasks.append(RunnerTask("v3_weekly_review", _weekly_review_task, interval=weekly_review_interval))
    if health_check_enabled:
        tasks.append(RunnerTask("v3_health_check", _health_task, interval=health_interval))

    return tuple(tasks)
