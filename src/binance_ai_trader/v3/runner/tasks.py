"""V3 runner task factory — builds RunnerTask objects for the V3 pipeline.

Tasks (returned by build_v3_tasks):
  v3_hotlist_scan   — every 15min: generate candidates → pipeline → paper orders
  v3_paper_settle   — every 15min: settle open V3 orders
  v3_shadow_report  — every 1h:    📊 V3 Paper Portfolio
  v3_health_check   — every 6h:    health ping (future)

Startup notification:
  send_v3_startup() is called from cli.py before the runner loop starts.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
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
from binance_ai_trader.v3.settlement.settler import V3Settler
from binance_ai_trader.v3.strategies.hotlist import HotlistStrategyV3
from binance_ai_trader.v3.telegram.notifier import V3TelegramNotifier
from binance_ai_trader.v3.telegram.shadow_report import V3ShadowReporter

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
    shadow_report_enabled: bool = True,
    health_check_enabled: bool = True,
    dedup_hours: int = 24,
    max_open_orders: int = 5,
) -> tuple[RunnerTask, ...]:
    """Bootstrap V3 tables and return runner tasks."""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    cand_repo  = V3CandidateRepository(db_path)
    push_repo  = V3PushQueueRepository(db_path)
    order_repo = V3PaperOrderRepository(db_path)
    perf_calc  = V3PerformanceCalculator(order_repo)

    strategy   = HotlistStrategyV3(client, universe_config)
    risk_cfg   = RiskConfig(strategy_id=_STRATEGY_ID, max_open_orders=max_open_orders)
    pipeline   = V3Pipeline(db_path, dedup_hours=dedup_hours, risk_config=risk_cfg)
    settler    = V3Settler(order_repo, client)

    v3_tg    = V3TelegramNotifier(telegram) if telegram else None
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

        # convert pushed candidates → paper orders
        orders_created = 0
        for candidate in result.candidates:
            if order_repo.exists_open_for_symbol_direction(
                _STRATEGY_ID, candidate.symbol, candidate.direction
            ):
                continue

            expires_at = (now + timedelta(hours=_HOLD_HOURS)).isoformat(timespec="seconds")
            from decimal import Decimal
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

            if v3_tg:
                v3_tg.send_candidate(candidate, hold_hours=_HOLD_HOURS)

            # Mark push_queue entry as SENT (pipeline already set candidate status=PUSHED)
            push_items = push_repo.load_by_signal(candidate.signal_id)
            if push_items:
                push_repo.mark_sent(push_id=push_items[0].push_id)

        log.info(
            "[V3] scan done — pushed=%d orders_created=%d blocked=%d",
            result.pushed, orders_created, result.total_blocked,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "scanned":       result.scanned,
            "pushed":        result.pushed,
            "orders_created": orders_created,
            "blocked_risk":  result.blocked_risk,
            "blocked_dedup": result.blocked_dedup,
        })

    def _settle_task() -> RunnerTaskResult:
        updated = settler.settle_all()
        return RunnerTaskResult("SUCCEEDED", {"settled": updated})

    def _report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {})

    def _health_task() -> RunnerTaskResult:
        return RunnerTaskResult("SUCCEEDED", {})

    tasks: list[RunnerTask] = [
        RunnerTask("v3_hotlist_scan", _scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("v3_paper_settle", _settle_task, interval=settle_interval, startup_immediate=True),
    ]
    if reporter:
        tasks.append(RunnerTask("v3_shadow_report", _report_task, interval=report_interval, startup_immediate=True))
    if health_check_enabled:
        tasks.append(RunnerTask("v3_health_check", _health_task, interval=health_interval))

    return tuple(tasks)
