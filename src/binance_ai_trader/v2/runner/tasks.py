"""V2 runner task factory — builds RunnerTask objects for the V2 pipeline.

Tasks:
  v2_hotlist_scan   — every 15 min: scan → signal → risk → paper order + Telegram signal alert
  v2_paper_settle   — every 15 min: settle open V2 orders + write order_events
  v2_summary        — every  6 hrs: calculate performance + send Telegram summary
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from binance_ai_trader.config import UniverseConfig
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.runner.engine import RunnerTask, RunnerTaskResult
from binance_ai_trader.v2.order_events.repository import V2OrderEventRepository
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
from binance_ai_trader.v2.telegram.notifier import V2TelegramNotifier

log = logging.getLogger(__name__)

_STRATEGY_ID = "hotlist_momentum_v2"


def build_v2_tasks(
    db_path: Path,
    universe_config: UniverseConfig,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
    max_retries: int = 3,
    telegram: TelegramNotifier | None = None,
    scan_interval: timedelta = timedelta(minutes=15),
    settle_interval: timedelta = timedelta(minutes=15),
    summary_interval: timedelta = timedelta(hours=6),
) -> tuple[RunnerTask, ...]:
    """Bootstrap V2 tables, register strategy, return runner tasks."""
    client = BinancePublicClient(
        base_url=base_url,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )

    strategy_repo = V2StrategyRepository(db_path)
    signal_repo = V2SignalRepository(db_path)
    order_repo = V2PaperOrderRepository(db_path)
    event_repo = V2OrderEventRepository(db_path)

    strategy_repo.ensure_table()
    signal_repo.ensure_table()
    order_repo.ensure_table()
    event_repo.ensure_table()

    strategy = register_hotlist_momentum_v2(db_path)

    risk_engine = V2RiskEngine(order_repo)
    hotlist_v2 = HotlistMomentumV2(client, universe_config, signal_repo, strategy)
    settler = V2Settler(order_repo, event_repo, client)
    perf_calc = V2PerformanceCalculator(order_repo)
    v2_tg = V2TelegramNotifier(telegram) if telegram else None

    def _scan_task() -> RunnerTaskResult:
        current_strategy = strategy_repo.get(_STRATEGY_ID)
        if current_strategy is None or not current_strategy.enabled:
            return RunnerTaskResult("SKIPPED", {"reason": "strategy disabled"})

        new_signals = hotlist_v2.scan()
        orders_created = 0

        for signal in new_signals:
            decision = risk_engine.check(signal, current_strategy)
            if not decision.allowed:
                log.info("[V2] risk rejected %s %s: %s", signal.symbol, signal.direction, decision.reason)
                continue

            from datetime import UTC, datetime
            from datetime import timedelta as td
            now = datetime.now(UTC)
            expires_at = now + td(hours=current_strategy.max_hold_hours)
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
            order_repo.save(order)
            orders_created += 1
            log.info("[V2] paper order created: %s %s", order.symbol, order.direction)

            from binance_ai_trader.v2.order_events.repository import V2OrderEvent, make_event_id
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

            if v2_tg:
                v2_tg.send_signal(signal)

        return RunnerTaskResult("SUCCEEDED", {
            "signals": len(new_signals),
            "orders_created": orders_created,
        })

    def _settle_task() -> RunnerTaskResult:
        updated = settler.settle_all()
        return RunnerTaskResult("SUCCEEDED", {"settled": updated})

    def _summary_task() -> RunnerTaskResult:
        perf = perf_calc.calculate(_STRATEGY_ID)
        if v2_tg:
            v2_tg.send_summary(perf)
        return RunnerTaskResult("SUCCEEDED", {
            "orders": perf.orders,
            "win_rate": str(perf.win_rate),
        })

    return (
        RunnerTask("v2_hotlist_scan", _scan_task, interval=scan_interval, startup_immediate=True),
        RunnerTask("v2_paper_settle", _settle_task, interval=settle_interval, startup_immediate=True),
        RunnerTask("v2_summary", _summary_task, interval=summary_interval),
    )
