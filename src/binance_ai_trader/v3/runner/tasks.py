"""V3 runner task factory — builds RunnerTask objects for the V3 pipeline.

Tasks (returned by build_v3_tasks):
  v3_hotlist_scan    — every 15min: generate candidates → pipeline → paper orders
  v3_paper_settle    — every 15min: settle open V3 orders
  v3_shadow_report   — every 1h:   📊 V3 Paper Portfolio
  v3_weekly_review   — every 7d:   📋 Weekly Strategy Review
  v3_health_check    — every 6h:   health ping

Tasks (returned by build_v66_tasks):
  v66_scan           — every 15min: V1-style watchlist (top6 gainers+losers, stop≤5%)
  v66_settle         — every 15min: settle open V66 paper orders
  v66_report         — every 1h:   📊 V66 Paper Portfolio

All permanent data stored in PostgreSQL via PG repositories.
"""
from __future__ import annotations

import json
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
from binance_ai_trader.v3.settings.repository import V3RuntimeSettingsRepository
from binance_ai_trader.v3.settlement.settler import V3Settler
from binance_ai_trader.v3.strategies.hotlist import HotlistStrategyV3
from binance_ai_trader.v3.strategies.reversal import (
    BREAKEVEN_TRIGGER_R,
    MAX_HOLD_MINUTES,
    HotlistStrategyReversal,
)
from binance_ai_trader.v3.strategies.v66 import HotlistStrategyV66
from binance_ai_trader.v3.strategies.v662 import HotlistStrategyV662
from binance_ai_trader.v3.strategies.v663 import HotlistStrategyV663
from binance_ai_trader.v3.strategies.v664 import HotlistStrategyV664
from binance_ai_trader.v3.strategies.wave_long import WaveLongStrategy
from binance_ai_trader.v3.strategies.wave_short import WaveShortStrategy
from binance_ai_trader.sma120.strategy import SMA120Strategy
from binance_ai_trader.sma120.config import (
    STRATEGY_ID as _SMA120_STRATEGY_ID,
    SYMBOL      as _SMA120_SYMBOL,
    HOLD_HOURS  as _SMA120_HOLD_HOURS,
    MAX_DAILY_TRADES as _SMA120_MAX_DAILY,
)
from binance_ai_trader.sma120.telegram import send_sma120_signal
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
    live_sync_interval: timedelta = timedelta(minutes=3),
    live_report_interval: timedelta = timedelta(hours=1),
    live_orphan_sweep_interval: timedelta = timedelta(minutes=30),
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
    settler    = V3Settler(
        order_repo, client, notifier=telegram,
        live_repo=(live_mirror._repo if live_mirror else None),
    )
    settings_repo = V3RuntimeSettingsRepository()

    v3_tg    = V3TelegramNotifier(telegram) if telegram else None
    live_reporter = (
        LiveHourlyReporter(live_mirror, live_mirror._repo, telegram,
                           strategy_id=_STRATEGY_ID, tag="V3")
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
        try:
            live_dedup_hours, live_max_open_orders = settings_repo.resolve(_STRATEGY_ID)
            live_risk_cfg = RiskConfig(
                strategy_id=_STRATEGY_ID,
                max_open_orders=live_max_open_orders,
                blacklist=risk_cfg.blacklist,
                blocked_regimes=risk_cfg.blocked_regimes,
            )
        except Exception:
            log.exception("[V3] failed to read runtime settings — using deploy defaults")
            live_dedup_hours, live_risk_cfg = dedup_hours, risk_cfg
        result = pipeline.run(strategy, now=now, dedup_hours=live_dedup_hours, risk_config=live_risk_cfg)

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
        updated = settler.settle_all(strategy_id=_STRATEGY_ID)
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

    def _live_orphan_sweep_task() -> RunnerTaskResult:
        # Runs off the V3 engine, but the sweep itself is unscoped and checks
        # ALL live orders (V3 + V66) against Binance — only needs to run once
        # regardless of how many strategies have live mirrors configured.
        if live_mirror:
            info = live_mirror.sweep_orphans()
            cleanup = live_mirror.cleanup_dangling_algo_orders()
            return RunnerTaskResult("SUCCEEDED", {
                "event_type": "v3_live_orphan_sweep",
                "checked": info["checked"],
                "orphans": len(info["orphans"]),
                "dangling_algo_checked": cleanup["checked"],
                "dangling_algo_cleaned": cleanup["cleaned"],
            })
        return RunnerTaskResult("SKIPPED")

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
        tasks.append(RunnerTask("v3_live_orphan_sweep", _live_orphan_sweep_task, interval=live_orphan_sweep_interval))
    if reporter:
        tasks.append(RunnerTask("v3_shadow_report", _report_task, interval=report_interval, startup_immediate=True))
    tasks.append(RunnerTask("v3_weekly_review", _weekly_review_task, interval=weekly_review_interval))
    if health_check_enabled:
        tasks.append(RunnerTask("v3_health_check", _health_task, interval=health_interval))

    return tuple(tasks)


# ─────────────────────────────────────────────────────────────────────────────
# V66 Task Builder — V1-style watchlist strategy (paper only)
# ─────────────────────────────────────────────────────────────────────────────

_V66_STRATEGY_ID = "hotlist_v66"
_V66_HOLD_HOURS  = 24


def build_v66_tasks(
    db_path: Path,
    universe_config: UniverseConfig,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    report_interval: timedelta = timedelta(hours=1),
    dedup_hours: int = 24,
    max_open_orders: int = 5,
    live_mirror: LiveMirrorEngine | None = None,
    live_sync_interval: timedelta = timedelta(minutes=3),
    live_report_interval: timedelta = timedelta(hours=1),
) -> tuple[RunnerTask, ...]:
    """Bootstrap V66 (V1-style watchlist) tasks. Paper trading always runs;
    live mirroring is optional and controlled independently of V3 (own
    LiveMirrorEngine instance, own strategy_id, own DB live_enabled flag)."""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    strategy   = HotlistStrategyV66(client, universe_config)
    risk_cfg   = RiskConfig(strategy_id=_V66_STRATEGY_ID, max_open_orders=max_open_orders)
    pipeline   = V3Pipeline(db_path, dedup_hours=dedup_hours, risk_config=risk_cfg)
    order_repo = V3PaperOrderRepository()
    push_repo  = V3PushQueueRepository()
    perf_calc  = V3PerformanceCalculator(order_repo)
    settler    = V3Settler(
        order_repo, client, notifier=telegram,
        live_repo=(live_mirror._repo if live_mirror else None),
    )
    settings_repo = V3RuntimeSettingsRepository()

    v66_tg = V3TelegramNotifier(telegram) if telegram else None
    live_reporter = (
        LiveHourlyReporter(live_mirror, live_mirror._repo, telegram,
                           strategy_id=_V66_STRATEGY_ID, tag="V66")
        if live_mirror and telegram else None
    )
    reporter = (
        V3ShadowReporter(
            telegram, order_repo, perf_calc, _V66_STRATEGY_ID,
            client=client,
            scan_interval_minutes=int(scan_interval.total_seconds() // 60),
            settle_interval_minutes=int(settle_interval.total_seconds() // 60),
            summary_interval_hours=int(report_interval.total_seconds() // 3600),
        )
        if telegram else None
    )

    def _v66_scan_task() -> RunnerTaskResult:
        now = datetime.now(UTC)
        try:
            live_dedup_hours, live_max_open_orders = settings_repo.resolve(_V66_STRATEGY_ID)
            live_risk_cfg = RiskConfig(
                strategy_id=_V66_STRATEGY_ID,
                max_open_orders=live_max_open_orders,
                blacklist=risk_cfg.blacklist,
                blocked_regimes=risk_cfg.blocked_regimes,
            )
        except Exception:
            log.exception("[V66] failed to read runtime settings — using deploy defaults")
            live_dedup_hours, live_risk_cfg = dedup_hours, risk_cfg
        result = pipeline.run(strategy, now=now, dedup_hours=live_dedup_hours, risk_config=live_risk_cfg)

        orders_created = 0
        for candidate in result.candidates:
            if order_repo.exists_open_for_symbol_direction(
                _V66_STRATEGY_ID, candidate.symbol, candidate.direction
            ):
                continue

            expires_at = (now + timedelta(hours=_V66_HOLD_HOURS)).isoformat(timespec="seconds")
            order = V3PaperOrder(
                order_id=make_order_id(),
                signal_id=candidate.signal_id,
                strategy_id=_V66_STRATEGY_ID,
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

            live_prefix = "【V66 模拟盘】"
            if live_mirror and live_mirror.is_enabled():
                try:
                    live_result = live_mirror.try_place(candidate)
                    live_prefix = live_result.prefix()
                except Exception:
                    log.exception("[V66] live mirror try_place failed for %s", candidate.signal_id)
                    live_prefix = "【实盘未下单：内部错误】"

            if v66_tg:
                v66_tg.send_candidate(candidate, hold_hours=_V66_HOLD_HOURS, live_prefix=live_prefix)

            push_items = push_repo.load_by_signal(candidate.signal_id)
            if push_items:
                push_repo.mark_sent(push_id=push_items[0].push_id)

        log.info(
            "[V66] scan done — pushed=%d orders_created=%d blocked=%d",
            result.pushed, orders_created, result.total_blocked,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":     "v66_scan",
            "scanned":        result.scanned,
            "pushed":         result.pushed,
            "orders_created": orders_created,
            "blocked_risk":   result.blocked_risk,
            "blocked_dedup":  result.blocked_dedup,
        })

    def _v66_settle_task() -> RunnerTaskResult:
        updated = settler.settle_all(strategy_id=_V66_STRATEGY_ID)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v66_settle", "settled": updated})

    def _v66_report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v66_report"})

    def _v66_live_sync_task() -> RunnerTaskResult:
        if live_mirror and live_mirror.is_enabled():
            updated = live_mirror.sync_all()
            return RunnerTaskResult("SUCCEEDED", {"event_type": "v66_live_sync", "updated": updated})
        return RunnerTaskResult("SKIPPED")

    def _v66_live_report_task() -> RunnerTaskResult:
        if live_mirror and live_mirror.is_enabled() and live_reporter:
            live_reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v66_live_report"})

    tasks: list[RunnerTask] = [
        RunnerTask("v66_scan",   _v66_scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("v66_settle", _v66_settle_task, interval=settle_interval, startup_immediate=True),
    ]
    if live_mirror:
        tasks.append(RunnerTask("v66_live_sync",   _v66_live_sync_task,   interval=live_sync_interval,   startup_immediate=True))
        tasks.append(RunnerTask("v66_live_report", _v66_live_report_task, interval=live_report_interval))
    tasks.append(RunnerTask("v66_report", _v66_report_task, interval=report_interval))

    return tuple(tasks)


# ─────────────────────────────────────────────────────────────────────────────
# V662 Task Builder — V66 升级版（量比+1h/4h趋势+止损收紧），paper only
# ─────────────────────────────────────────────────────────────────────────────

_V662_STRATEGY_ID = "hotlist_v662"
_V662_HOLD_HOURS  = 24


def build_v662_tasks(
    db_path: Path,
    universe_config: UniverseConfig,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    report_interval: timedelta = timedelta(hours=1),
    dedup_hours: int = 24,
    max_open_orders: int = 5,
) -> tuple[RunnerTask, ...]:
    """Bootstrap V662 tasks. Paper-only — 量比+趋势门槛升级版，无实盘镜像。"""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    strategy   = HotlistStrategyV662(client, universe_config)
    risk_cfg   = RiskConfig(strategy_id=_V662_STRATEGY_ID, max_open_orders=max_open_orders)
    pipeline   = V3Pipeline(db_path, dedup_hours=dedup_hours, risk_config=risk_cfg)
    order_repo = V3PaperOrderRepository()
    push_repo  = V3PushQueueRepository()
    perf_calc  = V3PerformanceCalculator(order_repo)
    settler    = V3Settler(order_repo, client, notifier=telegram, live_repo=None)
    settings_repo = V3RuntimeSettingsRepository()

    v662_tg = V3TelegramNotifier(telegram) if telegram else None
    reporter = (
        V3ShadowReporter(
            telegram, order_repo, perf_calc, _V662_STRATEGY_ID,
            client=client,
            scan_interval_minutes=int(scan_interval.total_seconds() // 60),
            settle_interval_minutes=int(settle_interval.total_seconds() // 60),
            summary_interval_hours=int(report_interval.total_seconds() // 3600),
        )
        if telegram else None
    )

    def _v662_scan_task() -> RunnerTaskResult:
        now = datetime.now(UTC)
        try:
            live_dedup_hours, live_max_open_orders = settings_repo.resolve(_V662_STRATEGY_ID)
            live_risk_cfg = RiskConfig(
                strategy_id=_V662_STRATEGY_ID,
                max_open_orders=live_max_open_orders,
                blacklist=risk_cfg.blacklist,
                blocked_regimes=risk_cfg.blocked_regimes,
            )
        except Exception:
            log.exception("[V662] failed to read runtime settings — using deploy defaults")
            live_dedup_hours, live_risk_cfg = dedup_hours, risk_cfg
        result = pipeline.run(strategy, now=now, dedup_hours=live_dedup_hours, risk_config=live_risk_cfg)

        orders_created = 0
        for candidate in result.candidates:
            if order_repo.exists_open_for_symbol_direction(
                _V662_STRATEGY_ID, candidate.symbol, candidate.direction
            ):
                continue

            expires_at = (now + timedelta(hours=_V662_HOLD_HOURS)).isoformat(timespec="seconds")
            order = V3PaperOrder(
                order_id=make_order_id(),
                signal_id=candidate.signal_id,
                strategy_id=_V662_STRATEGY_ID,
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

            if v662_tg:
                v662_tg.send_candidate(candidate, hold_hours=_V662_HOLD_HOURS, live_prefix="【V662 模拟盘】")

            push_items = push_repo.load_by_signal(candidate.signal_id)
            if push_items:
                push_repo.mark_sent(push_id=push_items[0].push_id)

        log.info(
            "[V662] scan done — pushed=%d orders_created=%d blocked=%d",
            result.pushed, orders_created, result.total_blocked,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":     "v662_scan",
            "scanned":        result.scanned,
            "pushed":         result.pushed,
            "orders_created": orders_created,
            "blocked_risk":   result.blocked_risk,
            "blocked_dedup":  result.blocked_dedup,
        })

    def _v662_settle_task() -> RunnerTaskResult:
        updated = settler.settle_all(strategy_id=_V662_STRATEGY_ID)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v662_settle", "settled": updated})

    def _v662_report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v662_report"})

    tasks: list[RunnerTask] = [
        RunnerTask("v662_scan",   _v662_scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("v662_settle", _v662_settle_task, interval=settle_interval, startup_immediate=True),
        RunnerTask("v662_report", _v662_report_task, interval=report_interval),
    ]
    return tuple(tasks)


# ─────────────────────────────────────────────────────────────────────────────
# V663 Task Builder — EMA三线排列升级版（EMA10>20>50），paper only
# ─────────────────────────────────────────────────────────────────────────────

_V663_STRATEGY_ID = "hotlist_v663"
_V663_HOLD_HOURS  = 24


def build_v663_tasks(
    db_path: Path,
    universe_config: UniverseConfig,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    report_interval: timedelta = timedelta(hours=1),
    dedup_hours: int = 24,
    max_open_orders: int = 5,
    live_mirror: LiveMirrorEngine | None = None,
    live_sync_interval: timedelta = timedelta(minutes=3),
    live_report_interval: timedelta = timedelta(hours=1),
) -> tuple[RunnerTask, ...]:
    """Bootstrap V663 tasks. Paper trading always runs; live mirroring
    optional — controlled by live_mirror engine + DB live_enabled flag."""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    strategy   = HotlistStrategyV663(client, universe_config)
    risk_cfg   = RiskConfig(strategy_id=_V663_STRATEGY_ID, max_open_orders=max_open_orders)
    pipeline   = V3Pipeline(db_path, dedup_hours=dedup_hours, risk_config=risk_cfg)
    order_repo = V3PaperOrderRepository()
    push_repo  = V3PushQueueRepository()
    perf_calc  = V3PerformanceCalculator(order_repo)
    settler    = V3Settler(
        order_repo, client, notifier=telegram,
        live_repo=(live_mirror._repo if live_mirror else None),
    )
    settings_repo = V3RuntimeSettingsRepository()

    v663_tg = V3TelegramNotifier(telegram) if telegram else None
    live_reporter = (
        LiveHourlyReporter(live_mirror, live_mirror._repo, telegram,
                           strategy_id=_V663_STRATEGY_ID, tag="V663")
        if live_mirror and telegram else None
    )
    reporter = (
        V3ShadowReporter(
            telegram, order_repo, perf_calc, _V663_STRATEGY_ID,
            client=client,
            scan_interval_minutes=int(scan_interval.total_seconds() // 60),
            settle_interval_minutes=int(settle_interval.total_seconds() // 60),
            summary_interval_hours=int(report_interval.total_seconds() // 3600),
        )
        if telegram else None
    )

    def _v663_scan_task() -> RunnerTaskResult:
        now = datetime.now(UTC)
        try:
            live_dedup_hours, live_max_open_orders = settings_repo.resolve(_V663_STRATEGY_ID)
            live_risk_cfg = RiskConfig(
                strategy_id=_V663_STRATEGY_ID,
                max_open_orders=live_max_open_orders,
                blacklist=risk_cfg.blacklist,
                blocked_regimes=risk_cfg.blocked_regimes,
            )
        except Exception:
            log.exception("[V663] failed to read runtime settings — using deploy defaults")
            live_dedup_hours, live_risk_cfg = dedup_hours, risk_cfg
        result = pipeline.run(strategy, now=now, dedup_hours=live_dedup_hours, risk_config=live_risk_cfg)

        orders_created = 0
        for candidate in result.candidates:
            if order_repo.exists_open_for_symbol_direction(
                _V663_STRATEGY_ID, candidate.symbol, candidate.direction
            ):
                continue

            expires_at = (now + timedelta(hours=_V663_HOLD_HOURS)).isoformat(timespec="seconds")
            order = V3PaperOrder(
                order_id=make_order_id(),
                signal_id=candidate.signal_id,
                strategy_id=_V663_STRATEGY_ID,
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

            live_prefix = "【V663 模拟盘】"
            if live_mirror and live_mirror.is_enabled():
                try:
                    live_result = live_mirror.try_place(candidate)
                    live_prefix = live_result.prefix()
                except Exception:
                    log.exception("[V663] live mirror try_place failed for %s", candidate.signal_id)
                    live_prefix = "【实盘未下单：内部错误】"

            if v663_tg:
                v663_tg.send_candidate(candidate, hold_hours=_V663_HOLD_HOURS, live_prefix=live_prefix)

            push_items = push_repo.load_by_signal(candidate.signal_id)
            if push_items:
                push_repo.mark_sent(push_id=push_items[0].push_id)

        log.info(
            "[V663] scan done — pushed=%d orders_created=%d blocked=%d",
            result.pushed, orders_created, result.total_blocked,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":     "v663_scan",
            "scanned":        result.scanned,
            "pushed":         result.pushed,
            "orders_created": orders_created,
            "blocked_risk":   result.blocked_risk,
            "blocked_dedup":  result.blocked_dedup,
        })

    def _v663_settle_task() -> RunnerTaskResult:
        updated = settler.settle_all(strategy_id=_V663_STRATEGY_ID)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v663_settle", "settled": updated})

    def _v663_report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v663_report"})

    def _v663_live_sync_task() -> RunnerTaskResult:
        if live_mirror and live_mirror.is_enabled():
            updated = live_mirror.sync_all()
            return RunnerTaskResult("SUCCEEDED", {"event_type": "v663_live_sync", "updated": updated})
        return RunnerTaskResult("SKIPPED")

    def _v663_live_report_task() -> RunnerTaskResult:
        if live_mirror and live_mirror.is_enabled() and live_reporter:
            live_reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v663_live_report"})

    tasks: list[RunnerTask] = [
        RunnerTask("v663_scan",   _v663_scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("v663_settle", _v663_settle_task, interval=settle_interval, startup_immediate=True),
    ]
    if live_mirror:
        tasks.append(RunnerTask("v663_live_sync",   _v663_live_sync_task,   interval=live_sync_interval,   startup_immediate=True))
        tasks.append(RunnerTask("v663_live_report", _v663_live_report_task, interval=live_report_interval))
    tasks.append(RunnerTask("v663_report", _v663_report_task, interval=report_interval))
    return tuple(tasks)


# ─────────────────────────────────────────────────────────────────────────────
# V664 Task Builder — 精准回踩+量缩，多空双向，paper only
# ─────────────────────────────────────────────────────────────────────────────

_V664_STRATEGY_ID = "hotlist_v664"
_V664_HOLD_HOURS  = 24


def build_v664_tasks(
    db_path: Path,
    universe_config: UniverseConfig,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    report_interval: timedelta = timedelta(hours=1),
    dedup_hours: int = 24,
    max_open_orders: int = 5,
) -> tuple[RunnerTask, ...]:
    """Bootstrap V664 tasks. Paper-only — 精准回踩EMA20+量缩，多空双向，无实盘镜像。"""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    strategy   = HotlistStrategyV664(client, universe_config)
    risk_cfg   = RiskConfig(strategy_id=_V664_STRATEGY_ID, max_open_orders=max_open_orders)
    pipeline   = V3Pipeline(db_path, dedup_hours=dedup_hours, risk_config=risk_cfg)
    order_repo = V3PaperOrderRepository()
    push_repo  = V3PushQueueRepository()
    perf_calc  = V3PerformanceCalculator(order_repo)
    settler    = V3Settler(order_repo, client, notifier=telegram, live_repo=None)
    settings_repo = V3RuntimeSettingsRepository()

    v664_tg = V3TelegramNotifier(telegram) if telegram else None
    reporter = (
        V3ShadowReporter(
            telegram, order_repo, perf_calc, _V664_STRATEGY_ID,
            client=client,
            scan_interval_minutes=int(scan_interval.total_seconds() // 60),
            settle_interval_minutes=int(settle_interval.total_seconds() // 60),
            summary_interval_hours=int(report_interval.total_seconds() // 3600),
        )
        if telegram else None
    )

    def _v664_scan_task() -> RunnerTaskResult:
        now = datetime.now(UTC)
        try:
            live_dedup_hours, live_max_open_orders = settings_repo.resolve(_V664_STRATEGY_ID)
            live_risk_cfg = RiskConfig(
                strategy_id=_V664_STRATEGY_ID,
                max_open_orders=live_max_open_orders,
                blacklist=risk_cfg.blacklist,
                blocked_regimes=risk_cfg.blocked_regimes,
            )
        except Exception:
            log.exception("[V664] failed to read runtime settings — using deploy defaults")
            live_dedup_hours, live_risk_cfg = dedup_hours, risk_cfg
        result = pipeline.run(strategy, now=now, dedup_hours=live_dedup_hours, risk_config=live_risk_cfg)

        orders_created = 0
        for candidate in result.candidates:
            if order_repo.exists_open_for_symbol_direction(
                _V664_STRATEGY_ID, candidate.symbol, candidate.direction
            ):
                continue

            expires_at = (now + timedelta(hours=_V664_HOLD_HOURS)).isoformat(timespec="seconds")
            order = V3PaperOrder(
                order_id=make_order_id(),
                signal_id=candidate.signal_id,
                strategy_id=_V664_STRATEGY_ID,
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

            if v664_tg:
                v664_tg.send_candidate(candidate, hold_hours=_V664_HOLD_HOURS, live_prefix="【V664 模拟盘】")

            push_items = push_repo.load_by_signal(candidate.signal_id)
            if push_items:
                push_repo.mark_sent(push_id=push_items[0].push_id)

        log.info(
            "[V664] scan done — pushed=%d orders_created=%d blocked=%d",
            result.pushed, orders_created, result.total_blocked,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":     "v664_scan",
            "scanned":        result.scanned,
            "pushed":         result.pushed,
            "orders_created": orders_created,
            "blocked_risk":   result.blocked_risk,
            "blocked_dedup":  result.blocked_dedup,
        })

    def _v664_settle_task() -> RunnerTaskResult:
        updated = settler.settle_all(strategy_id=_V664_STRATEGY_ID)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v664_settle", "settled": updated})

    def _v664_report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "v664_report"})

    tasks: list[RunnerTask] = [
        RunnerTask("v664_scan",   _v664_scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("v664_settle", _v664_settle_task, interval=settle_interval, startup_immediate=True),
        RunnerTask("v664_report", _v664_report_task, interval=report_interval),
    ]
    return tuple(tasks)


# ─────────────────────────────────────────────────────────────────────────────
# hotlist_reversal Task Builder — V-Reversal (山寨妖币反插针) strategy, paper only
# ─────────────────────────────────────────────────────────────────────────────

_REVERSAL_STRATEGY_ID = "hotlist_reversal"


def build_reversal_tasks(
    db_path: Path,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    report_interval: timedelta = timedelta(hours=1),
    dedup_hours: int = 24,
    max_open_orders: int = 5,
) -> tuple[RunnerTask, ...]:
    """Bootstrap hotlist_reversal tasks. Paper-only — no live mirror wiring.

    Independent from V3/V66: own strategy_id, own risk config, own stats
    (V3PerformanceCalculator already scopes by strategy_id).
    """
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    strategy   = HotlistStrategyReversal(client)
    risk_cfg   = RiskConfig(strategy_id=_REVERSAL_STRATEGY_ID, max_open_orders=max_open_orders)
    pipeline   = V3Pipeline(db_path, dedup_hours=dedup_hours, risk_config=risk_cfg)
    order_repo = V3PaperOrderRepository()
    push_repo  = V3PushQueueRepository()
    perf_calc  = V3PerformanceCalculator(order_repo)
    settler    = V3Settler(order_repo, client, notifier=telegram, live_repo=None)
    settings_repo = V3RuntimeSettingsRepository()

    rev_tg = V3TelegramNotifier(telegram) if telegram else None
    reporter = (
        V3ShadowReporter(
            telegram, order_repo, perf_calc, _REVERSAL_STRATEGY_ID,
            client=client,
            scan_interval_minutes=int(scan_interval.total_seconds() // 60),
            settle_interval_minutes=int(settle_interval.total_seconds() // 60),
            summary_interval_hours=int(report_interval.total_seconds() // 3600),
        )
        if telegram else None
    )

    def _reversal_scan_task() -> RunnerTaskResult:
        now = datetime.now(UTC)
        try:
            live_dedup_hours, live_max_open_orders = settings_repo.resolve(_REVERSAL_STRATEGY_ID)
            live_risk_cfg = RiskConfig(
                strategy_id=_REVERSAL_STRATEGY_ID,
                max_open_orders=live_max_open_orders,
                blacklist=risk_cfg.blacklist,
                blocked_regimes=risk_cfg.blocked_regimes,
            )
        except Exception:
            log.exception("[REV] failed to read runtime settings — using deploy defaults")
            live_dedup_hours, live_risk_cfg = dedup_hours, risk_cfg
        result = pipeline.run(strategy, now=now, dedup_hours=live_dedup_hours, risk_config=live_risk_cfg)

        orders_created = 0
        for candidate in result.candidates:
            if order_repo.exists_open_for_symbol_direction(
                _REVERSAL_STRATEGY_ID, candidate.symbol, candidate.direction
            ):
                continue

            entry = Decimal(candidate.entry)
            stop_loss = Decimal(candidate.sl)
            expires_at = (now + timedelta(minutes=MAX_HOLD_MINUTES)).isoformat(timespec="seconds")
            metadata = {
                "max_hold_minutes": MAX_HOLD_MINUTES,
                "breakeven_trigger_r": str(BREAKEVEN_TRIGGER_R),
                "orig_stop_loss": str(stop_loss),
                "breakeven_activated": False,
            }
            order = V3PaperOrder(
                order_id=make_order_id(),
                signal_id=candidate.signal_id,
                strategy_id=_REVERSAL_STRATEGY_ID,
                symbol=candidate.symbol,
                direction=candidate.direction,
                entry=entry,
                stop_loss=stop_loss,
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
                metadata_json=json.dumps(metadata),
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

            if rev_tg:
                rev_tg.send_candidate(
                    candidate,
                    hold_hours=MAX_HOLD_MINUTES / 60,
                    live_prefix="【V-Reversal 模拟盘】",
                )

            push_items = push_repo.load_by_signal(candidate.signal_id)
            if push_items:
                push_repo.mark_sent(push_id=push_items[0].push_id)

        log.info(
            "[REV] scan done — pushed=%d orders_created=%d blocked=%d",
            result.pushed, orders_created, result.total_blocked,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":     "hotlist_reversal_scan",
            "scanned":        result.scanned,
            "pushed":         result.pushed,
            "orders_created": orders_created,
            "blocked_risk":   result.blocked_risk,
            "blocked_dedup":  result.blocked_dedup,
        })

    def _reversal_settle_task() -> RunnerTaskResult:
        updated = settler.settle_all(strategy_id=_REVERSAL_STRATEGY_ID)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "hotlist_reversal_settle", "settled": updated})

    def _reversal_report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "hotlist_reversal_report"})

    tasks: list[RunnerTask] = [
        RunnerTask("hotlist_reversal_scan",   _reversal_scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("hotlist_reversal_settle", _reversal_settle_task, interval=settle_interval, startup_immediate=True),
    ]
    tasks.append(RunnerTask("hotlist_reversal_report", _reversal_report_task, interval=report_interval))

    return tuple(tasks)


# ─────────────────────────────────────────────────────────────────────────────
# Wave Long Breakout Task Builder — 放量突破回踩做多, paper-only
# ─────────────────────────────────────────────────────────────────────────────

_WAVE_LONG_STRATEGY_ID = "wave_long"
_WAVE_LONG_HOLD_HOURS  = 48


def build_wave_long_tasks(
    db_path: Path,
    universe_config: UniverseConfig,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    report_interval: timedelta = timedelta(hours=1),
    dedup_hours: int = 24,
    max_open_orders: int = 5,
) -> tuple[RunnerTask, ...]:
    """Bootstrap Wave Long Breakout tasks. Paper-only — 放量突破回踩做多，无实盘镜像。"""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    strategy   = WaveLongStrategy(client, universe_config, db_path)
    risk_cfg   = RiskConfig(strategy_id=_WAVE_LONG_STRATEGY_ID, max_open_orders=max_open_orders)
    pipeline   = V3Pipeline(db_path, dedup_hours=dedup_hours, risk_config=risk_cfg)
    order_repo = V3PaperOrderRepository()
    push_repo  = V3PushQueueRepository()
    perf_calc  = V3PerformanceCalculator(order_repo)
    settler    = V3Settler(order_repo, client, notifier=telegram, live_repo=None)
    settings_repo = V3RuntimeSettingsRepository()

    wave_long_tg = V3TelegramNotifier(telegram) if telegram else None
    reporter = (
        V3ShadowReporter(
            telegram, order_repo, perf_calc, _WAVE_LONG_STRATEGY_ID,
            client=client,
            scan_interval_minutes=int(scan_interval.total_seconds() // 60),
            settle_interval_minutes=int(settle_interval.total_seconds() // 60),
            summary_interval_hours=int(report_interval.total_seconds() // 3600),
        )
        if telegram else None
    )

    def _wave_long_scan_task() -> RunnerTaskResult:
        now = datetime.now(UTC)
        try:
            live_dedup_hours, live_max_open_orders = settings_repo.resolve(_WAVE_LONG_STRATEGY_ID)
            live_risk_cfg = RiskConfig(
                strategy_id=_WAVE_LONG_STRATEGY_ID,
                max_open_orders=live_max_open_orders,
                blacklist=risk_cfg.blacklist,
                blocked_regimes=risk_cfg.blocked_regimes,
            )
        except Exception:
            log.exception("[wave_long] failed to read runtime settings — using deploy defaults")
            live_dedup_hours, live_risk_cfg = dedup_hours, risk_cfg
        result = pipeline.run(strategy, now=now, dedup_hours=live_dedup_hours, risk_config=live_risk_cfg)

        orders_created = 0
        for candidate in result.candidates:
            if order_repo.exists_open_for_symbol_direction(
                _WAVE_LONG_STRATEGY_ID, candidate.symbol, candidate.direction
            ):
                continue

            expires_at = (now + timedelta(hours=_WAVE_LONG_HOLD_HOURS)).isoformat(timespec="seconds")
            order = V3PaperOrder(
                order_id=make_order_id(),
                signal_id=candidate.signal_id,
                strategy_id=_WAVE_LONG_STRATEGY_ID,
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

            if wave_long_tg:
                wave_long_tg.send_candidate(candidate, hold_hours=_WAVE_LONG_HOLD_HOURS, live_prefix="【Wave↑ 模拟盘】")

            push_items = push_repo.load_by_signal(candidate.signal_id)
            if push_items:
                push_repo.mark_sent(push_id=push_items[0].push_id)

        log.info(
            "[wave_long] scan done — pushed=%d orders_created=%d blocked=%d",
            result.pushed, orders_created, result.total_blocked,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":     "wave_long_scan",
            "scanned":        result.scanned,
            "pushed":         result.pushed,
            "orders_created": orders_created,
            "blocked_risk":   result.blocked_risk,
            "blocked_dedup":  result.blocked_dedup,
        })

    def _wave_long_settle_task() -> RunnerTaskResult:
        updated = settler.settle_all(strategy_id=_WAVE_LONG_STRATEGY_ID)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "wave_long_settle", "settled": updated})

    def _wave_long_report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "wave_long_report"})

    tasks: list[RunnerTask] = [
        RunnerTask("wave_long_scan",   _wave_long_scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("wave_long_settle", _wave_long_settle_task, interval=settle_interval, startup_immediate=True),
        RunnerTask("wave_long_report", _wave_long_report_task, interval=report_interval),
    ]
    return tuple(tasks)


# ─────────────────────────────────────────────────────────────────────────────
# Wave Short Breakdown Task Builder — 放量跌破反抽做空, paper-only
# ─────────────────────────────────────────────────────────────────────────────

_WAVE_SHORT_STRATEGY_ID = "wave_short"
_WAVE_SHORT_HOLD_HOURS  = 48


def build_wave_short_tasks(
    db_path: Path,
    universe_config: UniverseConfig,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    report_interval: timedelta = timedelta(hours=1),
    dedup_hours: int = 24,
    max_open_orders: int = 5,
) -> tuple[RunnerTask, ...]:
    """Bootstrap Wave Short Breakdown tasks. Paper-only — 放量跌破反抽做空，无实盘镜像。"""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    strategy   = WaveShortStrategy(client, universe_config, db_path)
    risk_cfg   = RiskConfig(strategy_id=_WAVE_SHORT_STRATEGY_ID, max_open_orders=max_open_orders)
    pipeline   = V3Pipeline(db_path, dedup_hours=dedup_hours, risk_config=risk_cfg)
    order_repo = V3PaperOrderRepository()
    push_repo  = V3PushQueueRepository()
    perf_calc  = V3PerformanceCalculator(order_repo)
    settler    = V3Settler(order_repo, client, notifier=telegram, live_repo=None)
    settings_repo = V3RuntimeSettingsRepository()

    wave_short_tg = V3TelegramNotifier(telegram) if telegram else None
    reporter = (
        V3ShadowReporter(
            telegram, order_repo, perf_calc, _WAVE_SHORT_STRATEGY_ID,
            client=client,
            scan_interval_minutes=int(scan_interval.total_seconds() // 60),
            settle_interval_minutes=int(settle_interval.total_seconds() // 60),
            summary_interval_hours=int(report_interval.total_seconds() // 3600),
        )
        if telegram else None
    )

    def _wave_short_scan_task() -> RunnerTaskResult:
        now = datetime.now(UTC)
        try:
            live_dedup_hours, live_max_open_orders = settings_repo.resolve(_WAVE_SHORT_STRATEGY_ID)
            live_risk_cfg = RiskConfig(
                strategy_id=_WAVE_SHORT_STRATEGY_ID,
                max_open_orders=live_max_open_orders,
                blacklist=risk_cfg.blacklist,
                blocked_regimes=risk_cfg.blocked_regimes,
            )
        except Exception:
            log.exception("[wave_short] failed to read runtime settings — using deploy defaults")
            live_dedup_hours, live_risk_cfg = dedup_hours, risk_cfg
        result = pipeline.run(strategy, now=now, dedup_hours=live_dedup_hours, risk_config=live_risk_cfg)

        orders_created = 0
        for candidate in result.candidates:
            if order_repo.exists_open_for_symbol_direction(
                _WAVE_SHORT_STRATEGY_ID, candidate.symbol, candidate.direction
            ):
                continue

            expires_at = (now + timedelta(hours=_WAVE_SHORT_HOLD_HOURS)).isoformat(timespec="seconds")
            order = V3PaperOrder(
                order_id=make_order_id(),
                signal_id=candidate.signal_id,
                strategy_id=_WAVE_SHORT_STRATEGY_ID,
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

            if wave_short_tg:
                wave_short_tg.send_candidate(candidate, hold_hours=_WAVE_SHORT_HOLD_HOURS, live_prefix="【Wave↓ 模拟盘】")

            push_items = push_repo.load_by_signal(candidate.signal_id)
            if push_items:
                push_repo.mark_sent(push_id=push_items[0].push_id)

        log.info(
            "[wave_short] scan done — pushed=%d orders_created=%d blocked=%d",
            result.pushed, orders_created, result.total_blocked,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":     "wave_short_scan",
            "scanned":        result.scanned,
            "pushed":         result.pushed,
            "orders_created": orders_created,
            "blocked_risk":   result.blocked_risk,
            "blocked_dedup":  result.blocked_dedup,
        })

    def _wave_short_settle_task() -> RunnerTaskResult:
        updated = settler.settle_all(strategy_id=_WAVE_SHORT_STRATEGY_ID)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "wave_short_settle", "settled": updated})

    def _wave_short_report_task() -> RunnerTaskResult:
        if reporter:
            reporter.send_report()
        return RunnerTaskResult("SUCCEEDED", {"event_type": "wave_short_report"})

    tasks: list[RunnerTask] = [
        RunnerTask("wave_short_scan",   _wave_short_scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("wave_short_settle", _wave_short_settle_task, interval=settle_interval, startup_immediate=True),
        RunnerTask("wave_short_report", _wave_short_report_task, interval=report_interval),
    ]
    return tuple(tasks)


# ─────────────────────────────────────────────────────────────────────────────
# Classic C1-C4 Task Builder — 经典量价策略，paper-only
# ─────────────────────────────────────────────────────────────────────────────

_CLASSIC_HOLD_HOURS = 48


def build_classic_tasks(
    db_path: Path,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 12.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    report_interval: timedelta = timedelta(hours=1),
) -> tuple[RunnerTask, ...]:
    """Bootstrap Classic C1-C4 tasks. Paper-only — never connects to live order engine."""
    from binance_ai_trader.classic.scanner import scan as classic_scan
    from binance_ai_trader.classic.repository import ClassicScanRepository
    from binance_ai_trader.classic.telegram_push import send_classic_signal
    from binance_ai_trader.classic.config import CFG
    from binance_ai_trader.classic.strategies.c1 import STRATEGY_ID as C1_ID
    from binance_ai_trader.classic.strategies.c2 import STRATEGY_ID as C2_ID
    from binance_ai_trader.classic.strategies.c3 import STRATEGY_ID as C3_ID
    from binance_ai_trader.classic.strategies.c4 import STRATEGY_ID_TOP as C4T_ID, STRATEGY_ID_BOT as C4B_ID

    _ALL_CLASSIC_STRATEGY_IDS = (C1_ID, C2_ID, C3_ID, C4T_ID, C4B_ID)

    client        = BinancePublicClient(base_url=base_url, timeout_seconds=timeout, max_retries=max_retries)
    order_repo    = V3PaperOrderRepository()
    push_repo     = V3PushQueueRepository()
    cand_repo     = V3CandidateRepository()
    settler       = V3Settler(order_repo, client, notifier=telegram, live_repo=None)
    scan_repo     = ClassicScanRepository()
    perf_calc     = V3PerformanceCalculator(order_repo)

    reporters = {}
    if telegram:
        for sid in _ALL_CLASSIC_STRATEGY_IDS:
            reporters[sid] = V3ShadowReporter(
                telegram, order_repo, perf_calc, sid,
                client=client,
                scan_interval_minutes=int(scan_interval.total_seconds() // 60),
                settle_interval_minutes=int(settle_interval.total_seconds() // 60),
                summary_interval_hours=int(report_interval.total_seconds() // 3600),
            )

    def _classic_scan_task() -> RunnerTaskResult:
        now      = datetime.now(UTC)
        now_iso  = now.isoformat(timespec="seconds")
        dedup_since = (now - timedelta(hours=CFG.dedup_hours)).isoformat(timespec="seconds")

        try:
            result = classic_scan(client, now)
        except Exception as exc:
            log.exception("[Classic] scan error: %s", exc)
            return RunnerTaskResult("FAILED", {"error": str(exc)})

        # Save all scan records to DB
        try:
            scan_repo.save_records(result.records)
        except Exception as exc:
            log.warning("[Classic] scan_records save failed: %s", exc)

        orders_created = 0
        for strategy_id, sig in result.signals.items():
            symbol    = sig["symbol"]
            direction = sig["direction"]

            # Dedup: skip if already open for this symbol/direction/strategy
            if order_repo.exists_open_for_symbol_direction(strategy_id, symbol, direction):
                log.info("[Classic/%s] dedup SKIP %s %s (open order exists)", strategy_id, symbol, direction)
                continue
            try:
                if scan_repo.exists_open_24h(symbol, direction, strategy_id, dedup_since):
                    log.info("[Classic/%s] dedup SKIP %s %s (24h record exists)", strategy_id, symbol, direction)
                    continue
            except Exception as exc:
                log.warning("[Classic/%s] exists_open_24h failed (table missing?): %s — proceeding", strategy_id, exc)

            # Generate signal_id
            try:
                signal_id = cand_repo.generate_signal_id(strategy_id, now)
            except Exception as exc:
                log.warning("[Classic/%s] signal_id generation failed: %s", strategy_id, exc)
                continue

            # Update scan record with signal_id
            scan_id = sig.get("_scan_id")
            if scan_id:
                try:
                    scan_repo.update_signal_id(scan_id, signal_id)
                except Exception as exc:
                    log.warning("[Classic/%s] update_signal_id failed: %s", strategy_id, exc)

            entry = Decimal(str(sig["entry"]))
            sl    = Decimal(str(sig["sl"]))
            tp1   = Decimal(str(sig["tp1"]))
            tp2   = Decimal(str(sig["tp2"]))
            rr    = Decimal(str(sig["rr"]))

            expires_at = (now + timedelta(hours=_CLASSIC_HOLD_HOURS)).isoformat(timespec="seconds")
            order = V3PaperOrder(
                order_id=make_order_id(),
                signal_id=signal_id,
                strategy_id=strategy_id,
                symbol=symbol,
                direction=direction,
                entry=entry,
                stop_loss=sl,
                tp1=tp1,
                tp2=tp2,
                rr=rr,
                status="OPEN",
                result=None,
                created_at=now_iso,
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
                signal_id=signal_id,
                event_type="CREATED",
                old_status=None,
                new_status="OPEN",
                candle_high=None,
                candle_low=None,
                triggered_at=now_iso,
                metadata_json="{}",
            ))
            orders_created += 1

            if telegram:
                try:
                    send_classic_signal(telegram, sig)
                except Exception as exc:
                    log.warning("[Classic/%s] telegram send failed: %s", strategy_id, exc)

            log.info(
                "[Classic/%s] SIGNAL %s %s entry=%s sl=%s tp1=%s score=%d grade=%s",
                strategy_id, symbol, direction, entry, sl, tp1,
                sig.get("score", 0), sig.get("vol_grade", "?"),
            )

        log.info(
            "[Classic] scan cycle done — coins=%d signals=%d orders_created=%d records=%d",
            result.total_coins, len(result.signals), orders_created, len(result.records),
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":     "classic_scan",
            "coins_scanned":  result.total_coins,
            "coins_evaluated": result.total_evaluated,
            "signals":        len(result.signals),
            "orders_created": orders_created,
            "records_saved":  len(result.records),
        })

    def _classic_settle_task() -> RunnerTaskResult:
        total = 0
        for sid in _ALL_CLASSIC_STRATEGY_IDS:
            try:
                total += settler.settle_all(strategy_id=sid)
            except Exception as exc:
                log.warning("[Classic] settle failed for %s: %s", sid, exc)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "classic_settle", "settled": total})

    def _classic_report_task() -> RunnerTaskResult:
        for sid, reporter in reporters.items():
            try:
                reporter.send_report()
            except Exception as exc:
                log.warning("[Classic] report failed for %s: %s", sid, exc)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "classic_report"})

    tasks: list[RunnerTask] = [
        RunnerTask("classic_scan",   _classic_scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("classic_settle", _classic_settle_task, interval=settle_interval, startup_immediate=True),
        RunnerTask("classic_report", _classic_report_task, interval=report_interval),
    ]
    return tuple(tasks)


# ── SMA120 V1.9-D — XAUUSDT single-symbol strategy ───────────────────────────

def build_sma120_tasks(
    base_url: str = "https://fapi.binance.com",
    timeout: float = 15.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=5),
    settle_interval: timedelta = timedelta(minutes=5),
    report_interval: timedelta = timedelta(hours=1),
) -> tuple[RunnerTask, ...]:
    """Bootstrap SMA120 V1.9-D paper-trading tasks for XAUUSDT futures.

    Entry: M15 EMA20/60 trend + M5 SMA120-extension pullback-breakout + H1 filter (long).
    Fixed: SL=$8, TP=$16 (1:2 RR), ATR∈[4.00,6.67], max 3 trades/day.
    """
    client     = BinancePublicClient(base_url=base_url, timeout_seconds=timeout, max_retries=max_retries)
    strategy   = SMA120Strategy(client)
    order_repo = V3PaperOrderRepository()
    perf_calc  = V3PerformanceCalculator(order_repo)
    settler    = V3Settler(order_repo, client, notifier=telegram, live_repo=None)
    reporter   = V3ShadowReporter(
        telegram, order_repo, perf_calc, _SMA120_STRATEGY_ID,
        client=client,
        scan_interval_minutes=int(scan_interval.total_seconds() // 60),
        settle_interval_minutes=int(settle_interval.total_seconds() // 60),
        summary_interval_hours=int(report_interval.total_seconds() // 3600),
    ) if telegram else None

    def _sma120_scan_task() -> RunnerTaskResult:
        now      = datetime.now(UTC)
        today    = now.strftime("%Y-%m-%d")

        # ── Runtime on/off toggle (Telegram /paperon sma120 / /paperoff sma120) ──
        # live_enabled=None → default ON (paper-only strategy, safe to run always)
        # live_enabled=False → user explicitly paused scanning via Telegram
        try:
            from binance_ai_trader.v3.settings.repository import V3RuntimeSettingsRepository, SMA120_STRATEGY_ID as _SETT_ID
            _sett = V3RuntimeSettingsRepository().get(_SETT_ID)
            if _sett.live_enabled is False:
                log.info("[SMA120] scanning paused via /paperoff — skipping")
                return RunnerTaskResult("SUCCEEDED", {"event_type": "sma120_scan", "paused": True})
        except Exception as _e:
            log.warning("[SMA120] settings check failed (non-fatal): %s", _e)

        # ── Daily trade limit ──────────────────────────────────────────
        all_orders = order_repo.load_all()
        today_count = sum(
            1 for o in all_orders
            if o.strategy_id == _SMA120_STRATEGY_ID
            and (o.created_at or "").startswith(today)
        )
        if today_count >= _SMA120_MAX_DAILY:
            log.info("[SMA120] daily limit %d/%d reached — skipping scan", today_count, _SMA120_MAX_DAILY)
            return RunnerTaskResult("SUCCEEDED", {"event_type": "sma120_scan", "daily_limit": True})

        # ── Run strategy ───────────────────────────────────────────────
        signal = strategy.scan()
        if signal is None:
            return RunnerTaskResult("SUCCEEDED", {"event_type": "sma120_scan", "signal": False})

        # ── Direction dedup: skip if already have open order same direction
        open_orders = order_repo.load_open_by_strategy(_SMA120_STRATEGY_ID)
        if any(o.direction == signal.direction for o in open_orders):
            log.info("[SMA120] open %s order exists — skipping duplicate", signal.direction)
            return RunnerTaskResult("SUCCEEDED", {"event_type": "sma120_scan", "skipped_dup": True})

        # ── Create paper order ─────────────────────────────────────────
        ts_seq   = int(now.timestamp()) % 100000
        signal_id = f"SMA-{now.strftime('%Y%m%d')}-{ts_seq:05d}"
        expires_at = (now + timedelta(hours=_SMA120_HOLD_HOURS)).isoformat(timespec="seconds")
        meta = json.dumps({
            "m5_atr":    str(signal.m5_atr),
            "m5_ema20":  str(signal.m5_ema20),
            "m5_sma120": str(signal.m5_sma120),
            "max_hold_minutes": _SMA120_HOLD_HOURS * 60,
        })

        order = V3PaperOrder(
            order_id=make_order_id(),
            signal_id=signal_id,
            strategy_id=_SMA120_STRATEGY_ID,
            symbol=_SMA120_SYMBOL,
            direction=signal.direction,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp1,
            rr=signal.rr,
            status="OPEN",
            result=None,
            created_at=now.isoformat(timespec="seconds"),
            filled_at=None,
            closed_at=None,
            expires_at=expires_at,
            pnl_pct=None,
            rr_realized=None,
            pushed=True,
            metadata_json=meta,
        )
        order_repo.save(order)
        order_repo.append_event(V3OrderEvent(
            event_id=make_event_id(),
            order_id=order.order_id,
            signal_id=signal_id,
            event_type="CREATED",
            old_status=None,
            new_status="OPEN",
            candle_high=None,
            candle_low=None,
            triggered_at=now.isoformat(timespec="seconds"),
            metadata_json=meta,
        ))

        if telegram:
            try:
                send_sma120_signal(telegram, signal, signal_id)
            except Exception as exc:
                log.warning("[SMA120] telegram send failed: %s", exc)

        log.info(
            "[SMA120] order created %s %s entry=%.2f SL=%.2f TP=%.2f today=%d/%d",
            signal_id, signal.direction, signal.entry, signal.stop_loss, signal.tp1,
            today_count + 1, _SMA120_MAX_DAILY,
        )
        return RunnerTaskResult("SUCCEEDED", {
            "event_type":  "sma120_scan",
            "signal":      True,
            "direction":   signal.direction,
            "signal_id":   signal_id,
            "today_count": today_count + 1,
        })

    def _sma120_settle_task() -> RunnerTaskResult:
        settled = settler.settle_all(strategy_id=_SMA120_STRATEGY_ID)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "sma120_settle", "settled": settled})

    def _sma120_report_task() -> RunnerTaskResult:
        if reporter:
            try:
                reporter.send_report()
            except Exception as exc:
                log.warning("[SMA120] report failed: %s", exc)
        return RunnerTaskResult("SUCCEEDED", {"event_type": "sma120_report"})

    return (
        RunnerTask("sma120_scan",   _sma120_scan_task,   interval=scan_interval,   startup_immediate=True),
        RunnerTask("sma120_settle", _sma120_settle_task, interval=settle_interval, startup_immediate=True),
        RunnerTask("sma120_report", _sma120_report_task, interval=report_interval),
    )
