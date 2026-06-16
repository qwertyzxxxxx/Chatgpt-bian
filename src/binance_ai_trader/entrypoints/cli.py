from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.application.analyze_market_regime import MarketRegimeAnalyzer
from binance_ai_trader.application.analyze_capital_flow import CapitalFlowAnalyzer
from binance_ai_trader.application.analyze_space import SpaceAnalyzer
from binance_ai_trader.application.analyze_sector_strength import SectorStrengthAnalyzer
from binance_ai_trader.application.collect_market_data import MarketDataCollector
from binance_ai_trader.application.collect_history import HistoricalDataCollector
from binance_ai_trader.application.evaluate_signals import SignalEvaluator
from binance_ai_trader.application.generate_signals import SignalGenerator
from binance_ai_trader.application.score_market_data import MarketScorer
from binance_ai_trader.backtest import BacktestEngine, BacktestPolicy
from binance_ai_trader.config import SectorConfig, UniverseConfig
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.hotlist import (
    HotlistAlert,
    HotlistAlertEngine,
    HotlistEntryPlan,
    HotlistFunnelAnalyzer,
    HotlistFunnelPolicy,
    HotlistPerformanceRepository,
    HotlistPerformanceTracker,
    HotlistWatcher,
    HotlistWatcherPolicy,
    HotlistWatchlist,
    HotlistWatchlistPolicy,
    HotlistWatchlistRepository,
    format_hotlist_ai_review_message,
    format_hotlist_alert_message,
    format_hotlist_funnel_message,
    format_hotlist_performance_summary,
    render_hotlist_daily_summary,
    render_hotlist_funnel,
    render_hotlist_performance,
    render_hotlist_top5_review,
    review_hotlist_opportunities,
)
from binance_ai_trader.paper.service import PaperSimulator
from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.operations import (
    build_ops_status,
    render_ops_daily,
    run_safety_audit,
)
from binance_ai_trader.reporting import DailyReportService, format_top3_message
from binance_ai_trader.runner import (
    HealthService,
    ProductionRunner,
    RunnerLockError,
    RunnerTaskResult,
    default_tasks,
)
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.strategy_lab.service import StrategyLab
from binance_ai_trader.strategy_lab.reporting import (
    render_champion_league_markdown,
    render_sweep_markdown,
    write_sweep_markdown,
)
from binance_ai_trader.strategy_lab.service import BREAKOUT_HUNTER_SWEEP_COMBINATIONS
from binance_ai_trader.walk_forward import (
    WalkForwardPolicy, WalkForwardValidator, render_markdown,
)


def _add_telegram_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--telegram-bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    parser.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"))
    parser.add_argument("--telegram-timeout", type=float, default=10.0)


def _telegram_notifier(args: argparse.Namespace) -> TelegramNotifier | None:
    token = getattr(args, "telegram_bot_token", None)
    chat_id = getattr(args, "telegram_chat_id", None)
    if not token and not chat_id:
        return None
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be configured together")
    return TelegramNotifier(token, chat_id, getattr(args, "telegram_timeout", 10.0))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Binance USD-M Futures read-only analysis")
    subparsers = parser.add_subparsers(dest="command", required=True)

    history = subparsers.add_parser(
        "collect-history", help="bootstrap resumable public historical market data"
    )
    history.add_argument("--days", type=int, default=180)
    history.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    history.add_argument("--config", type=Path, default=Path("config/universe.json"))
    history.add_argument("--sectors-config", type=Path, default=Path("config/sectors.json"))
    history.add_argument("--base-url", default="https://fapi.binance.com")
    history.add_argument("--end-ms", type=int)
    history.add_argument("--timeout", type=float, default=20.0)
    history.add_argument("--max-retries", type=int, default=5)
    history.add_argument("--request-pause", type=float, default=0.05)
    history.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    scan = subparsers.add_parser("scan", help="collect, score, and generate regime-directed signals")
    scan.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    scan.add_argument("--config", type=Path, default=Path("config/universe.json"))
    scan.add_argument("--sectors-config", type=Path, default=Path("config/sectors.json"))
    scan.add_argument("--base-url", default="https://fapi.binance.com")
    scan.add_argument("--kline-limit", type=int, default=200)
    scan.add_argument("--max-workers", type=int, default=5)
    scan.add_argument("--timeout", type=float, default=10.0)
    scan.add_argument("--max-retries", type=int, default=3)
    scan.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    regime = subparsers.add_parser("regime", help="analyze BTC/ETH market regime")
    regime.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    regime.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    sectors = subparsers.add_parser("sectors", help="rank sectors from latest scores")
    sectors.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    sectors.add_argument("--config", type=Path, default=Path("config/sectors.json"))
    sectors.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")


    capital = subparsers.add_parser("capital", help="calculate public capital-flow scores")
    capital.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    capital.add_argument("--base-url", default="https://fapi.binance.com")
    capital.add_argument("--timeout", type=float, default=10.0)
    capital.add_argument("--max-retries", type=int, default=3)
    capital.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    space = subparsers.add_parser("space", help="calculate 30/60/120 day directional space")
    space.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    space.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    backtest = subparsers.add_parser("backtest", help="replay LONG/SHORT strategies on stored klines")
    backtest.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    backtest.add_argument("--config", type=Path, default=Path("config/sectors.json"))
    backtest.add_argument("--start-ms", type=int)
    backtest.add_argument("--end-ms", type=int)
    backtest.add_argument("--step-bars", type=int, default=1)
    backtest.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    strategies = subparsers.add_parser("strategies", help="list and compare registered strategies")
    strategy_commands = strategies.add_subparsers(dest="strategies_command", required=True)
    strategy_list = strategy_commands.add_parser("list", help="list strategy versions")
    strategy_list.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    strategy_list.add_argument(
        "--baseline-config", type=Path, default=Path("config/strategies/baseline_v1.json")
    )
    strategy_list.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    strategy_compare = strategy_commands.add_parser("compare", help="compare strategies on common history")
    strategy_compare.add_argument("strategy_ids", nargs="+")
    strategy_compare.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    strategy_compare.add_argument("--sectors-config", type=Path, default=Path("config/sectors.json"))
    strategy_compare.add_argument(
        "--baseline-config", type=Path, default=Path("config/strategies/baseline_v1.json")
    )
    strategy_compare.add_argument("--start-ms", type=int)
    strategy_compare.add_argument("--end-ms", type=int)
    strategy_compare.add_argument("--step-bars", type=int, default=1)
    strategy_compare.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    strategy_rank = strategy_commands.add_parser(
        "rank", help="rank Phase 1 strategies from the latest successful backtest results"
    )
    strategy_rank.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    strategy_rank.add_argument(
        "--baseline-config", type=Path, default=Path("config/strategies/baseline_v1.json")
    )
    strategy_rank.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    strategy_sweep = strategy_commands.add_parser(
        "sweep", help="sweep Breakout Hunter parameters using the latest backtest"
    )
    strategy_sweep.add_argument("strategy_id")
    strategy_sweep.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    strategy_sweep.add_argument("--report", type=Path)
    strategy_sweep.add_argument(
        "--baseline-config", type=Path, default=Path("config/strategies/baseline_v1.json")
    )
    strategy_sweep.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    strategy_champion = strategy_commands.add_parser(
        "champion", help="select the weekly research champion from all strategies"
    )
    strategy_champion.add_argument(
        "--database", type=Path, default=Path("data/market_data.db")
    )
    strategy_champion.add_argument(
        "--baseline-config", type=Path, default=Path("config/strategies/baseline_v1.json")
    )
    strategy_champion.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    hotlist = subparsers.add_parser("hotlist", help="research public futures hotlists")
    hotlist_commands = hotlist.add_subparsers(dest="hotlist_command", required=True)
    for name in ("watch", "scan"):
        hotlist_watch = hotlist_commands.add_parser(
            name, help="produce research-only entry plans for high-momentum contracts"
        )
        hotlist_watch.add_argument("--limit", type=int, choices=range(1, 6), default=5)
        hotlist_watch.add_argument("--min-move-pct", type=Decimal, default=Decimal("15"))
        hotlist_watch.add_argument(
            "--min-quote-volume", type=Decimal, default=Decimal("5000000")
        )
        hotlist_watch.add_argument("--expiry-minutes", type=int, default=60)
        hotlist_watch.add_argument(
            "--database", type=Path, default=Path("data/market_data.db")
        )
        hotlist_watch.add_argument(
            "--config", type=Path, default=Path("config/universe.json")
        )
        hotlist_watch.add_argument("--base-url", default="https://fapi.binance.com")
        hotlist_watch.add_argument("--timeout", type=float, default=10.0)
        hotlist_watch.add_argument("--max-retries", type=int, default=3)
        hotlist_watch.add_argument(
            "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
        )
    hotlist_review = hotlist_commands.add_parser(
        "review", help="refresh and analyze the rolling hotlist observation pool"
    )
    hotlist_review.add_argument("--gainers", type=int, default=6)
    hotlist_review.add_argument("--losers", type=int, default=6)
    hotlist_review.add_argument(
        "--max-opportunities", type=int, choices=range(1, 4), default=3
    )
    hotlist_review.add_argument("--expiry-minutes", type=int, default=60)
    hotlist_review.add_argument("--max-ttl-minutes", type=int, default=120)
    hotlist_review.add_argument("--refresh-minutes", type=int, default=15)
    hotlist_review.add_argument("--min-rr", type=Decimal, default=Decimal("2"))
    hotlist_review.add_argument("--max-stop-pct", type=Decimal, default=Decimal("5"))
    hotlist_review.add_argument(
        "--min-quote-volume", type=Decimal, default=Decimal("5000000")
    )
    hotlist_review.add_argument(
        "--database", type=Path, default=Path("data/market_data.db")
    )
    hotlist_review.add_argument(
        "--config", type=Path, default=Path("config/universe.json")
    )
    hotlist_review.add_argument("--base-url", default="https://fapi.binance.com")
    hotlist_review.add_argument("--timeout", type=float, default=10.0)
    hotlist_review.add_argument("--max-retries", type=int, default=3)
    hotlist_review.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    hotlist_funnel = hotlist_commands.add_parser(
        "funnel", help="diagnostic funnel — trace why no signals are generated"
    )
    hotlist_funnel.add_argument("--min-move-pct", type=Decimal, default=Decimal("15"))
    hotlist_funnel.add_argument(
        "--min-quote-volume", type=Decimal, default=Decimal("5000000")
    )
    hotlist_funnel.add_argument("--min-rr", type=Decimal, default=Decimal("2"))
    hotlist_funnel.add_argument("--max-stop-pct", type=Decimal, default=Decimal("5"))
    hotlist_funnel.add_argument(
        "--database", type=Path, default=Path("data/market_data.db")
    )
    hotlist_funnel.add_argument(
        "--config", type=Path, default=Path("config/universe.json")
    )
    hotlist_funnel.add_argument("--base-url", default="https://fapi.binance.com")
    hotlist_funnel.add_argument("--timeout", type=float, default=10.0)
    hotlist_funnel.add_argument("--max-retries", type=int, default=3)
    hotlist_funnel.add_argument(
        "--report", type=Path, default=Path("reports/hotlist_funnel.md")
    )
    hotlist_funnel.add_argument("--send-telegram", action="store_true", default=False)
    _add_telegram_arguments(hotlist_funnel)
    hotlist_funnel.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    hotlist_alert = subparsers.add_parser(
        "hotlist-alert", help="generate deduplicated research alerts from the hotlist pool"
    )
    hotlist_alert.add_argument("--gainers", type=int, default=6)
    hotlist_alert.add_argument("--losers", type=int, default=6)
    hotlist_alert.add_argument("--expiry-minutes", type=int, default=60)
    hotlist_alert.add_argument("--max-ttl-minutes", type=int, default=120)
    hotlist_alert.add_argument("--refresh-minutes", type=int, default=15)
    hotlist_alert.add_argument(
        "--database", type=Path, default=Path("data/market_data.db")
    )
    hotlist_alert.add_argument(
        "--config", type=Path, default=Path("config/universe.json")
    )
    hotlist_alert.add_argument("--base-url", default="https://fapi.binance.com")
    hotlist_alert.add_argument("--timeout", type=float, default=10.0)
    hotlist_alert.add_argument("--max-retries", type=int, default=3)
    hotlist_alert.add_argument(
        "--report", type=Path, default=Path("reports/hotlist_daily_summary.md")
    )
    hotlist_alert.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    hotlist_ai_review = subparsers.add_parser(
        "hotlist-ai-review",
        help="rank and export the Top 5 public-data hotlist research plans",
    )
    hotlist_ai_review.add_argument("--limit", type=int, choices=range(1, 6), default=5)
    hotlist_ai_review.add_argument(
        "--min-move-pct", type=Decimal, default=Decimal("15")
    )
    hotlist_ai_review.add_argument(
        "--min-quote-volume", type=Decimal, default=Decimal("5000000")
    )
    hotlist_ai_review.add_argument("--expiry-minutes", type=int, default=60)
    hotlist_ai_review.add_argument(
        "--config", type=Path, default=Path("config/universe.json")
    )
    hotlist_ai_review.add_argument("--base-url", default="https://fapi.binance.com")
    hotlist_ai_review.add_argument("--timeout", type=float, default=10.0)
    hotlist_ai_review.add_argument("--max-retries", type=int, default=3)
    hotlist_ai_review.add_argument(
        "--report", type=Path, default=Path("reports/hotlist_top5_review.md")
    )
    hotlist_ai_review.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    hotlist_performance = subparsers.add_parser(
        "hotlist-performance",
        help="track and evaluate Hotlist AI Reviewer opportunities",
    )
    hotlist_performance.add_argument(
        "--database", type=Path, default=Path("data/market_data.db")
    )
    hotlist_performance.add_argument(
        "--config", type=Path, default=Path("config/universe.json")
    )
    hotlist_performance.add_argument("--base-url", default="https://fapi.binance.com")
    hotlist_performance.add_argument("--timeout", type=float, default=10.0)
    hotlist_performance.add_argument("--max-retries", type=int, default=3)
    hotlist_performance.add_argument(
        "--report", type=Path, default=Path("reports/hotlist_performance.md")
    )
    hotlist_performance.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    ops = subparsers.add_parser("ops", help="operational status, reports, and safety checks")
    ops_commands = ops.add_subparsers(dest="ops_command", required=True)
    for name in ("status", "daily", "safety-audit"):
        ops_command = ops_commands.add_parser(name)
        ops_command.add_argument(
            "--database", type=Path, default=Path("data/market_data.db")
        )
        ops_command.add_argument(
            "--baseline-config",
            type=Path,
            default=Path("config/strategies/baseline_v1.json"),
        )
        ops_command.add_argument(
            "--log-level",
            choices=("DEBUG", "INFO", "WARNING", "ERROR"),
            default="INFO",
        )
        if name == "daily":
            ops_command.add_argument(
                "--report", type=Path, default=Path("reports/ops_daily.md")
            )

    telegram = subparsers.add_parser("telegram", help="Telegram operational checks")
    telegram_commands = telegram.add_subparsers(dest="telegram_command", required=True)
    telegram_test = telegram_commands.add_parser(
        "hotlist-test", help="send one research-only sample alert"
    )
    telegram_test.add_argument("--telegram-timeout", type=float, default=10.0)
    telegram_test.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    auto_research = subparsers.add_parser("auto-research", aliases=["auto_research"], help="research 20 parameter sets and save the Top 10 candidates")
    auto_research.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    auto_research.add_argument("--sectors-config", type=Path, default=Path("config/sectors.json"))
    auto_research.add_argument(
        "--baseline-config", type=Path, default=Path("config/strategies/baseline_v1.json")
    )
    auto_research.add_argument("--max-candidates", type=int, default=10, choices=range(1, 11))
    auto_research.add_argument("--start-ms", type=int)
    auto_research.add_argument("--end-ms", type=int)
    auto_research.add_argument("--step-bars", type=int, default=1)
    auto_research.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")


    paper = subparsers.add_parser(
        "paper-simulate", help="apply completed evaluations to the aggressive paper ledger"
    )
    paper.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    paper.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    daily = subparsers.add_parser("daily-report", help="print the daily research and paper summary")
    daily.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    daily.add_argument("--date", type=date.fromisoformat)
    _add_telegram_arguments(daily)
    daily.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")


    run_loop = subparsers.add_parser(
        "run-loop", help="run the fault-isolated Reserved VM scheduler"
    )
    run_loop.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    run_loop.add_argument("--config", type=Path, default=Path("config/universe.json"))
    run_loop.add_argument("--sectors-config", type=Path, default=Path("config/sectors.json"))
    run_loop.add_argument(
        "--baseline-config", type=Path, default=Path("config/strategies/baseline_v1.json")
    )
    run_loop.add_argument("--base-url", default="https://fapi.binance.com")
    run_loop.add_argument("--kline-limit", type=int, default=200)
    run_loop.add_argument("--max-workers", type=int, default=5)
    run_loop.add_argument("--timeout", type=float, default=10.0)
    run_loop.add_argument("--max-retries", type=int, default=3)
    run_loop.add_argument("--research-step-bars", type=int, default=1)
    run_loop.add_argument("--poll-seconds", type=float, default=30.0)
    run_loop.add_argument("--lock-file", type=Path)
    run_loop.add_argument("--once", action="store_true")
    run_loop.add_argument("--enable-hotlist-alerts", action="store_true")
    run_loop.add_argument("--history-days", type=int, default=180)
    run_loop.add_argument("--history-interval-hours", type=float, default=24.0)
    run_loop.add_argument("--history-request-pause", type=float, default=0.05)
    _add_telegram_arguments(run_loop)
    run_loop.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    health = subparsers.add_parser("health", help="print runner and database health JSON")
    health.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    health.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    walk_forward = subparsers.add_parser(
        "walk-forward", help="run rolling train/validation/test evaluation"
    )
    walk_forward.add_argument("strategy_ids", nargs="*", default=("baseline_v1",))
    walk_forward.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    walk_forward.add_argument("--sectors-config", type=Path, default=Path("config/sectors.json"))
    walk_forward.add_argument(
        "--baseline-config", type=Path, default=Path("config/strategies/baseline_v1.json")
    )
    walk_forward.add_argument("--start-ms", type=int)
    walk_forward.add_argument("--end-ms", type=int)
    walk_forward.add_argument("--train-points", type=int, default=720)
    walk_forward.add_argument("--validation-points", type=int, default=240)
    walk_forward.add_argument("--test-points", type=int, default=240)
    walk_forward.add_argument("--step-points", type=int, default=240)
    walk_forward.add_argument("--embargo-points", type=int, default=96)
    walk_forward.add_argument("--point-stride", type=int, default=1)
    walk_forward.add_argument(
        "--report", type=Path, default=Path("reports/walk_forward_validation.md")
    )
    walk_forward.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    evaluate = subparsers.add_parser("evaluate", help="evaluate stored LONG/SHORT signals")
    evaluate.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    evaluate.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    gemini_committee = subparsers.add_parser(
        "gemini-committee", help="Gemini Committee V1 — AI final-selection from strategy candidates"
    )
    gemini_committee_cmds = gemini_committee.add_subparsers(dest="gc_command", required=True)
    _gc_review_p = gemini_committee_cmds.add_parser(
        "review", help="run Gemini committee review and pick the best opportunity"
    )
    _gc_review_p.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    _gc_review_p.add_argument("--ai-macro-database", type=Path, default=Path("data/ai_macro.db"))
    _gc_review_p.add_argument("--max-candidates", type=int, default=4)
    _gc_review_p.add_argument("--cooldown-hours", type=float, default=4.0)
    _gc_review_p.add_argument("--gemini-model", default="gemini-2.5-flash")
    _gc_review_p.add_argument("--base-url", default="https://fapi.binance.com")
    _gc_review_p.add_argument("--gemini-timeout", type=float, default=60.0)
    _gc_review_p.add_argument("--gemini-retries", type=int, default=2)
    _gc_review_p.add_argument("--send-telegram", action="store_true", default=False)
    _add_telegram_arguments(_gc_review_p)
    _gc_review_p.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    ai_macro = subparsers.add_parser(
        "ai-macro", help="AI Macro Trader V1 — research-only macro analysis and virtual trade tracking"
    )
    ai_macro_commands = ai_macro.add_subparsers(dest="ai_macro_command", required=True)

    _ai_macro_scan_p = ai_macro_commands.add_parser(
        "scan", help="run macro analysis, score candidates and create virtual trades"
    )
    _ai_macro_scan_p.add_argument("--database", type=Path, default=Path("data/ai_macro.db"))
    _ai_macro_scan_p.add_argument("--config", type=Path, default=Path("config/universe.json"))
    _ai_macro_scan_p.add_argument("--base-url", default="https://fapi.binance.com")
    _ai_macro_scan_p.add_argument("--timeout", type=float, default=10.0)
    _ai_macro_scan_p.add_argument("--max-retries", type=int, default=3)
    _ai_macro_scan_p.add_argument("--gainers", type=int, default=6)
    _ai_macro_scan_p.add_argument("--losers", type=int, default=6)
    _ai_macro_scan_p.add_argument("--report", type=Path, default=Path("reports/ai_macro_report.md"))
    _ai_macro_scan_p.add_argument("--send-telegram", action="store_true", default=False)
    _add_telegram_arguments(_ai_macro_scan_p)
    _ai_macro_scan_p.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    _ai_macro_review_p = ai_macro_commands.add_parser(
        "review", help="review open virtual trades against current prices"
    )
    _ai_macro_review_p.add_argument("--database", type=Path, default=Path("data/ai_macro.db"))
    _ai_macro_review_p.add_argument("--base-url", default="https://fapi.binance.com")
    _ai_macro_review_p.add_argument("--timeout", type=float, default=10.0)
    _ai_macro_review_p.add_argument("--max-retries", type=int, default=3)
    _ai_macro_review_p.add_argument("--send-telegram", action="store_true", default=False)
    _add_telegram_arguments(_ai_macro_review_p)
    _ai_macro_review_p.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    _ai_macro_settle_p = ai_macro_commands.add_parser(
        "settle", help="force-settle virtual trades that have exceeded 48 hours"
    )
    _ai_macro_settle_p.add_argument("--database", type=Path, default=Path("data/ai_macro.db"))
    _ai_macro_settle_p.add_argument("--base-url", default="https://fapi.binance.com")
    _ai_macro_settle_p.add_argument("--timeout", type=float, default=10.0)
    _ai_macro_settle_p.add_argument("--max-retries", type=int, default=3)
    _ai_macro_settle_p.add_argument("--send-telegram", action="store_true", default=False)
    _add_telegram_arguments(_ai_macro_settle_p)
    _ai_macro_settle_p.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    _ai_macro_perf_p = ai_macro_commands.add_parser(
        "performance", help="display AI macro virtual trading performance statistics"
    )
    _ai_macro_perf_p.add_argument("--database", type=Path, default=Path("data/ai_macro.db"))
    _ai_macro_perf_p.add_argument("--report", type=Path, default=Path("reports/ai_macro_performance.md"))
    _ai_macro_perf_p.add_argument("--send-telegram", action="store_true", default=False)
    _add_telegram_arguments(_ai_macro_perf_p)
    _ai_macro_perf_p.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"scan", "regime", "sectors", "backtest", "evaluate", "strategies", "hotlist", "hotlist-alert", "hotlist-ai-review", "hotlist-performance", "ops", "telegram", "auto-research", "auto_research", "paper-simulate", "daily-report", "run-loop", "health", "capital", "space", "walk-forward", "collect-history", "ai-macro", "gemini-committee", "-h", "--help"}:
        arguments.insert(0, "scan")
    args = build_parser().parse_args(arguments)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "evaluate":
        return _evaluate(args.database)
    if args.command == "collect-history":
        return _collect_history(args)
    if args.command == "walk-forward":
        return _walk_forward(args)
    if args.command == "run-loop":
        return _run_loop(args)
    if args.command == "health":
        return _health(args.database)
    if args.command == "capital":
        return _capital(args)
    if args.command == "space":
        return _space(args.database)
    if args.command == "strategies":
        return _strategies(args)
    if args.command == "hotlist":
        return _hotlist(args)
    if args.command == "hotlist-alert":
        return _hotlist_alert(args)
    if args.command == "hotlist-ai-review":
        return _hotlist_ai_review(args)
    if args.command == "hotlist-performance":
        return _hotlist_performance(args)
    if args.command == "ops":
        return _ops(args)
    if args.command == "telegram":
        return _telegram(args)
    if args.command in {"auto-research", "auto_research"}:
        return _auto_research(args)
    if args.command == "paper-simulate":
        return _paper_simulate(args.database)
    if args.command == "daily-report":
        return _daily_report(args.database, args.date, _telegram_notifier(args))
    if args.command == "backtest":
        return _backtest(args)
    if args.command == "regime":
        return _regime(args.database)
    if args.command == "sectors":
        return _sectors(args.database, args.config)
    if args.command == "ai-macro":
        return _ai_macro(args)
    if args.command == "gemini-committee":
        return _gemini_committee(args)
    return _scan(args)


def _collect_history(args: argparse.Namespace) -> int:
    client = BinancePublicClient(args.base_url, args.timeout, args.max_retries)
    repository = MarketDataRepository(args.database)
    try:
        result = HistoricalDataCollector(
            client,
            repository,
            UniverseConfig.load(args.config),
            SectorConfig.load(args.sectors_config),
            args.request_pause,
        ).collect(args.days, args.end_ms)
    finally:
        repository.close()
    print(json.dumps({
        "run_id": result.run_id,
        "symbols": result.symbols,
        "symbol_count": len(result.symbols),
        "start_ms": result.start_ms,
        "end_ms": result.end_ms,
        "fetched_klines": result.fetched_klines,
        "capital_observations": result.capital_observations,
        "universe_snapshots": result.universe_snapshots,
        "failures": result.failures,
        "database": str(args.database),
    }, separators=(",", ":"), sort_keys=True))
    return 2 if result.failures else 0


def _scan(args: argparse.Namespace) -> int:
    sector_config = SectorConfig.load(args.sectors_config)
    sector_map = SectorMap(sector_config.symbol_to_sector)
    client = BinancePublicClient(args.base_url, args.timeout, args.max_retries)
    repository = MarketDataRepository(args.database)
    try:
        result = MarketDataCollector(
            client=client,
            repository=repository,
            universe_config=UniverseConfig.load(args.config),
            kline_limit=args.kline_limit,
            max_workers=args.max_workers,
        ).collect()
        snapshot = repository.load_snapshot_for_run(result.run_id)
        market_regime = MarketRegimeAnalyzer(repository).analyze(snapshot.snapshot_id)
        failed_symbols = {failure.split("/", 1)[0] for failure in result.failed_requests}
        scoring_result = MarketScorer(repository).score_run(
            run_id=result.run_id,
            symbols=(member.symbol for member in result.universe),
            excluded_symbols=failed_symbols,
            snapshot_id=snapshot.snapshot_id,
        )
        SectorStrengthAnalyzer(repository, sector_map).analyze_latest(snapshot.snapshot_id)
        CapitalFlowAnalyzer(repository, client).analyze_latest(snapshot_id=snapshot.snapshot_id)
        SpaceAnalyzer(repository, client).analyze_latest(snapshot_id=snapshot.snapshot_id)
        signal_result = SignalGenerator(repository, sector_map=sector_map).generate_latest(
            snapshot.snapshot_id
        )
    finally:
        repository.close()

    for signal in signal_result.signals:
        print(
            json.dumps(
                {
                    "snapshot_id": signal_result.snapshot_id,
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "score": signal.score,
                    "combined_regime": signal.combined_regime,
                    "sector": signal.sector,
                    "sector_rank": signal.sector_rank,
                    "capital_score": signal.capital_score,
                    "space_score": signal.space_score,
                    "final_signal_score": signal.final_signal_score,
                    "data_quality_status": signal.data_quality_status,
                    "data_quality": signal.data_quality or {},
                    "entry": str(signal.entry),
                    "latest_close": str(signal.latest_close),
                    "stop_loss": str(signal.stop_loss),
                    "stop_loss_pct": str(signal.stop_loss_pct),
                    "TP1": str(signal.tp1),
                    "TP2": str(signal.tp2),
                    "rr_tp1": str(signal.rr_tp1),
                    "rr_tp2": str(signal.rr_tp2),
                    "logic_summary": signal.logic_summary,
                },
                separators=(",", ":"),
            )
        )
    logging.info(
        "Regime %s; scored %d symbols and generated %d signals for run %s; skipped scoring for %d",
        market_regime.combined_regime,
        len(scoring_result.ranked_scores),
        len(signal_result.signals),
        result.run_id,
        len(scoring_result.skipped_symbols),
    )
    return 2 if result.failed_requests else 0


def _regime(database: Path) -> int:
    repository = MarketDataRepository(database)
    try:
        regime = MarketRegimeAnalyzer(repository).analyze()
    finally:
        repository.close()
    print(
        json.dumps(
            {
                "btc_regime": regime.btc_regime,
                "eth_regime": regime.eth_regime,
                "combined_regime": regime.combined_regime,
                "data_quality_status": regime.data_quality_status,
            },
            separators=(",", ":"),
        )
    )
    return 0


def _sectors(database: Path, config_path: Path) -> int:
    sector_config = SectorConfig.load(config_path)
    repository = MarketDataRepository(database)
    try:
        snapshots = SectorStrengthAnalyzer(
            repository,
            SectorMap(sector_config.symbol_to_sector),
        ).analyze_latest()
    finally:
        repository.close()
    for snapshot in snapshots:
        print(
            json.dumps(
                {
                    "sector": snapshot.sector,
                    "sector_rank": snapshot.sector_rank,
                    "member_count": snapshot.member_count,
                    "avg_score": str(snapshot.avg_score),
                    "median_score": str(snapshot.median_score),
                    "top3_avg_score": str(snapshot.top3_avg_score),
                    "positive_24h_ratio": str(snapshot.positive_24h_ratio),
                    "quote_volume_24h": str(snapshot.quote_volume_24h),
                    "data_quality_status": snapshot.data_quality_status,
                },
                separators=(",", ":"),
            )
        )
    return 0


def _walk_forward(args: argparse.Namespace) -> int:
    sector_config = SectorConfig.load(args.sectors_config)
    repository = MarketDataRepository(args.database)
    try:
        lab = StrategyLab(
            repository,
            SectorMap(sector_config.symbol_to_sector),
            args.baseline_config,
        )
        versions = {item.strategy_id: item for item in lab.list_versions()}
        missing = [item for item in args.strategy_ids if item not in versions]
        if missing:
            raise ValueError(f"unknown strategies: {', '.join(missing)}")
        report = WalkForwardValidator(
            repository,
            SectorMap(sector_config.symbol_to_sector),
            WalkForwardPolicy(
                train_points=args.train_points,
                validation_points=args.validation_points,
                test_points=args.test_points,
                step_points=args.step_points,
                embargo_points=args.embargo_points,
            ),
        ).run(
            tuple(versions[item].config for item in args.strategy_ids),
            args.start_ms,
            args.end_ms,
            args.point_stride,
        )
        render_markdown(report, args.report)
    finally:
        repository.close()

    print(json.dumps({
        "candidate_strategy_ids": report.candidate_strategy_ids,
        "folds": [
            {
                "window": fold.window.index,
                "selected_strategy_id": fold.selected_strategy_id,
                "train": _walk_forward_metrics(fold.train_metrics),
                "validation": _walk_forward_metrics(fold.validation_metrics),
                "test": _walk_forward_metrics(fold.test_metrics),
                "overfitting_risks": fold.overfitting_risks,
            }
            for fold in report.folds
        ],
        "report": str(args.report),
    }, separators=(",", ":"), sort_keys=True))
    return 0


def _walk_forward_metrics(metrics) -> dict[str, object]:
    return {
        "win_rate": metrics.tp1_hit_rate,
        "profit_factor": metrics.profit_factor,
        "max_drawdown_r": metrics.max_drawdown_r,
        "number_of_trades": metrics.total_signals,
    }


def _backtest(args: argparse.Namespace) -> int:
    sector_config = SectorConfig.load(args.config)
    repository = MarketDataRepository(args.database)
    try:
        summary = BacktestEngine(
            repository,
            SectorMap(sector_config.symbol_to_sector),
            BacktestPolicy(step_bars=args.step_bars),
        ).run(args.start_ms, args.end_ms)
    finally:
        repository.close()

    print(
        json.dumps(
            {
                "run_id": summary.run_id,
                "started_at": summary.started_at,
                "completed_at": summary.completed_at,
                "evaluation_points": summary.evaluation_points,
                **asdict(summary.metrics),
                "by_direction": {
                    key: asdict(value) for key, value in summary.by_direction.items()
                },
                "by_regime": {
                    key: asdict(value) for key, value in summary.by_combined_regime.items()
                },
                "by_combined_regime": {
                    key: asdict(value) for key, value in summary.by_combined_regime.items()
                },
                "by_sector": {key: asdict(value) for key, value in summary.by_sector.items()},
                "by_score_bucket": {
                    key: asdict(value) for key, value in summary.by_score_bucket.items()
                },
                "by_capital_bucket": {
                    key: asdict(value) for key, value in summary.by_capital_bucket.items()
                },
                "by_space_bucket": {
                    key: asdict(value) for key, value in summary.by_space_bucket.items()
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _strategies(args: argparse.Namespace) -> int:
    repository = MarketDataRepository(args.database)
    try:
        if args.strategies_command == "list":
            versions = StrategyLab(
                repository, SectorMap({}), args.baseline_config
            ).list_versions()
            for version in versions:
                print(json.dumps(_strategy_version_json(version), separators=(",", ":"), sort_keys=True))
            return 0
        if args.strategies_command == "rank":
            rankings = StrategyLab(
                repository, SectorMap({}), args.baseline_config
            ).rank()
            for ranking in rankings:
                print(json.dumps(_ranking_json(ranking), separators=(",", ":"), sort_keys=True))
            return 0
        if args.strategies_command == "sweep":
            results = StrategyLab(
                repository, SectorMap({}), args.baseline_config
            ).sweep(args.strategy_id)
            if args.report is not None:
                write_sweep_markdown(
                    args.report,
                    render_sweep_markdown(
                        args.strategy_id,
                        args.database,
                        results,
                        BREAKOUT_HUNTER_SWEEP_COMBINATIONS,
                    ),
                )
            for result in results:
                print(json.dumps(_sweep_json(result), separators=(",", ":"), sort_keys=True))
            return 0
        if args.strategies_command == "champion":
            standings = StrategyLab(
                repository, SectorMap({}), args.baseline_config
            ).champion_league()
            report = Path("reports/champion_league.md")
            write_sweep_markdown(
                report,
                render_champion_league_markdown(args.database, standings),
            )
            print(
                json.dumps(
                    {
                        "champion": _champion_json(standings[0]) if standings else None,
                        "leaderboard": [_champion_json(item) for item in standings],
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0
        sector_config = SectorConfig.load(args.sectors_config)
        comparisons = StrategyLab(
            repository,
            SectorMap(sector_config.symbol_to_sector),
            args.baseline_config,
        ).compare(tuple(args.strategy_ids), args.start_ms, args.end_ms, args.step_bars)
    finally:
        repository.close()
    for comparison in comparisons:
        print(json.dumps(_comparison_json(comparison), separators=(",", ":"), sort_keys=True))
    return 0


def _auto_research(args: argparse.Namespace) -> int:
    sector_config = SectorConfig.load(args.sectors_config)
    repository = MarketDataRepository(args.database)
    try:
        comparisons = StrategyLab(
            repository,
            SectorMap(sector_config.symbol_to_sector),
            args.baseline_config,
        ).auto_research(
            args.max_candidates, args.start_ms, args.end_ms, args.step_bars
        )
    finally:
        repository.close()
    for rank, comparison in enumerate(comparisons, start=1):
        payload = _comparison_json(comparison)
        payload.update({"rank": rank, "status": "candidate"})
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


def _hotlist(args: argparse.Namespace) -> int:
    client = BinancePublicClient(
        args.base_url,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    if args.hotlist_command == "funnel":
        return _hotlist_funnel(args, client)
    if args.hotlist_command == "review":
        repository = HotlistWatchlistRepository(args.database)
        try:
            plans = HotlistWatchlist(
                client,
                repository,
                UniverseConfig.load(args.config),
                HotlistWatchlistPolicy(
                    gainers=args.gainers,
                    losers=args.losers,
                    max_opportunities=args.max_opportunities,
                    expiry_minutes=args.expiry_minutes,
                    max_ttl_minutes=args.max_ttl_minutes,
                    refresh_minutes=args.refresh_minutes,
                    min_rr=args.min_rr,
                    max_stop_pct=args.max_stop_pct,
                    min_quote_volume=args.min_quote_volume,
                ),
            ).review()
        finally:
            repository.close()
    else:
        plans = HotlistWatcher(
            client,
            UniverseConfig.load(args.config),
            HotlistWatcherPolicy(
                limit=args.limit,
                min_move_pct=args.min_move_pct,
                min_quote_volume=args.min_quote_volume,
                expiry_minutes=args.expiry_minutes,
            ),
        ).watch()
    for plan in plans:
        payload = {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(plan).items()
        }
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


def _hotlist_funnel(args: argparse.Namespace, client) -> int:
    repository = HotlistWatchlistRepository(args.database)
    try:
        report = HotlistFunnelAnalyzer(
            client,
            repository,
            UniverseConfig.load(args.config),
            HotlistFunnelPolicy(
                min_move_pct=args.min_move_pct,
                min_quote_volume=args.min_quote_volume,
                min_rr=args.min_rr,
                max_stop_pct=args.max_stop_pct,
            ),
        ).run()
    finally:
        repository.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_hotlist_funnel(report), encoding="utf-8")

    telegram_status: dict = {}
    if getattr(args, "send_telegram", False):
        token = getattr(args, "telegram_bot_token", None)
        chat_id = getattr(args, "telegram_chat_id", None)
        if not token or not chat_id:
            telegram_status = {"telegram": "SKIPPED", "telegram_skip_reason": "secrets_not_configured"}
        else:
            notifier = TelegramNotifier(token, chat_id, getattr(args, "telegram_timeout", 10.0))
            msg = format_hotlist_funnel_message(report)
            notifier.send(msg)
            telegram_status = {"telegram": "SENT", "telegram_chars": len(msg)}

    print(
        json.dumps(
            {
                "generated_at": report.generated_at,
                "parameters": report.parameters,
                "funnel": [
                    {
                        "label": step.label,
                        "count": step.count,
                        "dropped": step.dropped,
                        "drop_off_pct": step.drop_off_pct,
                    }
                    for step in report.steps
                ],
                "top_rejections": [
                    {
                        "symbol": r.symbol,
                        "reason": r.reason,
                        "detail": r.detail,
                    }
                    for r in report.top_rejections
                ],
                "final_opportunities": [
                    {
                        "symbol": o.symbol,
                        "direction": o.direction,
                        "entry": str(o.entry),
                        "stop_loss": str(o.stop_loss),
                        "tp1": str(o.tp1),
                        "tp2": str(o.tp2),
                        "rr": str(o.rr),
                    }
                    for o in report.final_opportunities
                ],
                "report": str(args.report),
                "research_only": report.research_only,
                **telegram_status,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _hotlist_alert(args: argparse.Namespace) -> int:
    client = BinancePublicClient(
        args.base_url,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    repository = HotlistWatchlistRepository(args.database)
    try:
        review = HotlistWatchlist(
            client,
            repository,
            UniverseConfig.load(args.config),
            HotlistWatchlistPolicy(
                gainers=args.gainers,
                losers=args.losers,
                max_opportunities=3,
                expiry_minutes=args.expiry_minutes,
                max_ttl_minutes=args.max_ttl_minutes,
                refresh_minutes=args.refresh_minutes,
                min_rr=Decimal("2"),
                max_stop_pct=Decimal("5"),
                min_quote_volume=Decimal("5000000"),
            ),
        )
        alerts, summary = HotlistAlertEngine(review, repository).generate()
    finally:
        repository.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_hotlist_daily_summary(summary), encoding="utf-8")
    for alert in alerts:
        print(
            json.dumps(
                {
                    "symbol": alert.symbol,
                    "direction": alert.direction,
                    "entry": str(alert.entry),
                    "created_at": alert.created_at,
                    "level": alert.level,
                    "message": format_hotlist_alert_message(alert),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    return 0


def _hotlist_ai_review(args: argparse.Namespace) -> int:
    client = BinancePublicClient(
        args.base_url,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    plans = HotlistWatcher(
        client,
        UniverseConfig.load(args.config),
        HotlistWatcherPolicy(
            limit=args.limit,
            min_move_pct=args.min_move_pct,
            min_quote_volume=args.min_quote_volume,
            expiry_minutes=args.expiry_minutes,
        ),
    ).watch()
    reviews = review_hotlist_opportunities(plans, args.limit)
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_hotlist_top5_review(reviews, generated_at), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "generated_at": generated_at,
                "research_only": True,
                "reviews": [
                    {
                        key: str(value) if isinstance(value, Decimal) else value
                        for key, value in asdict(review).items()
                    }
                    for review in reviews
                ],
                "telegram_message": format_hotlist_ai_review_message(reviews),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _hotlist_performance(args: argparse.Namespace) -> int:
    client = BinancePublicClient(
        args.base_url,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    now = datetime.now(UTC)
    plans = HotlistWatcher(
        client,
        UniverseConfig.load(args.config),
        HotlistWatcherPolicy(
            limit=5,
            min_move_pct=Decimal("15"),
            min_quote_volume=Decimal("5000000"),
            expiry_minutes=60,
        ),
    ).watch(now)
    reviews = review_hotlist_opportunities(plans)
    repository = HotlistPerformanceRepository(args.database)
    try:
        tracker = HotlistPerformanceTracker(client, repository)
        tracker.track(reviews, now)
        tracker.evaluate(now)
        statistics = tracker.statistics()
        opportunities = repository.opportunities(limit=50)
        outcomes = repository.outcomes()
    finally:
        repository.close()
    generated_at = now.isoformat(timespec="seconds")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_hotlist_performance(
            statistics, opportunities, outcomes, generated_at
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "generated_at": generated_at,
                "research_only": True,
                "statistics": {
                    "total_opportunities": statistics.total_opportunities,
                    "win_rate": str(statistics.win_rate),
                    "tp1_rate": str(statistics.tp1_rate),
                    "tp2_rate": str(statistics.tp2_rate),
                    "average_rr": str(statistics.average_rr),
                    "average_return": str(statistics.average_return),
                    "confidence_performance": [
                        {
                            key: str(value) if isinstance(value, Decimal) else value
                            for key, value in asdict(item).items()
                        }
                        for item in statistics.confidence_performance
                    ],
                },
                "telegram_message": format_hotlist_performance_summary(statistics),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _run_hotlist_alert_task(
    args: argparse.Namespace, notifier: TelegramNotifier | None
) -> RunnerTaskResult:
    if notifier is None:
        return RunnerTaskResult(
            status="SKIPPED",
            details={
                "alerts_generated": 0,
                "alerts_sent": 0,
                "skipped_reason": "telegram_not_configured",
            },
        )
    client = BinancePublicClient(
        args.base_url,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    repository = HotlistWatchlistRepository(args.database)
    try:
        review = HotlistWatchlist(
            client,
            repository,
            UniverseConfig.load(args.config),
            HotlistWatchlistPolicy(),
        )
        alerts, summary = HotlistAlertEngine(review, repository).generate()
    finally:
        repository.close()
    report = Path("reports/hotlist_daily_summary.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_hotlist_daily_summary(summary), encoding="utf-8")
    for alert in alerts:
        notifier.send(format_hotlist_alert_message(alert))
    return RunnerTaskResult(
        details={
            "alerts_generated": len(alerts),
            "alerts_sent": len(alerts),
            "skipped_reason": None,
        }
    )


def _paper_simulate(database: Path) -> int:
    repository = MarketDataRepository(database)
    try:
        summary = PaperSimulator(repository).simulate()
    finally:
        repository.close()
    print(json.dumps({
        "starting_equity": str(summary.starting_equity),
        "ending_equity": str(summary.ending_equity),
        "processed_trades": summary.processed_trades,
        "skipped_while_paused": summary.skipped_while_paused,
        "mode": summary.mode,
        "consecutive_losses": summary.consecutive_losses,
        "paused_until": summary.paused_until,
        "current_target": summary.current_target,
        "aggressive_allowed": summary.aggressive_allowed,
        "disclaimer": "Paper simulation only; no profit is guaranteed and no orders are placed.",
    }, separators=(",", ":"), sort_keys=True))
    return 0


def _daily_report(
    database: Path,
    report_date: date | None,
    notifier: TelegramNotifier | None = None,
) -> int:
    repository = MarketDataRepository(database)
    try:
        payload = DailyReportService(repository).build(report_date)
    finally:
        repository.close()
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    if notifier is not None:
        notifier.send(format_top3_message(payload))
    return 0


def _capital(args: argparse.Namespace) -> int:
    repository = MarketDataRepository(args.database)
    try:
        snapshots = CapitalFlowAnalyzer(
            repository, BinancePublicClient(args.base_url, args.timeout, args.max_retries)
        ).analyze_latest()
    finally:
        repository.close()
    for item in snapshots:
        print(json.dumps(asdict(item), default=str, separators=(",", ":"), sort_keys=True))
    return 0


def _space(database: Path) -> int:
    repository = MarketDataRepository(database)
    try:
        snapshots = SpaceAnalyzer(repository).analyze_latest()
    finally:
        repository.close()
    for item in snapshots:
        print(json.dumps(asdict(item), default=str, separators=(",", ":"), sort_keys=True))
    return 0


def _run_loop(args: argparse.Namespace) -> int:
    database = args.database
    lock_path = args.lock_file or Path(f"{database}.runner.lock")

    def invoke(arguments: list[str]) -> int:
        return main(arguments)

    token = getattr(args, "telegram_bot_token", None)
    chat_id = getattr(args, "telegram_chat_id", None)
    notifier = (
        TelegramNotifier(token, chat_id, getattr(args, "telegram_timeout", 10.0))
        if token and chat_id
        else None
    )

    def observe_task(event_type: str, status: str, error: str | None) -> None:
        if notifier is not None and status == "FAILED":
            notifier.send(f"Runner task failed: {event_type}\n{error or 'unknown error'}")

    tasks = default_tasks(
        scan=lambda: invoke([
            "scan", "--database", str(database), "--config", str(args.config),
            "--sectors-config", str(args.sectors_config), "--base-url", args.base_url,
            "--kline-limit", str(args.kline_limit), "--max-workers", str(args.max_workers),
            "--timeout", str(args.timeout), "--max-retries", str(args.max_retries),
            "--log-level", args.log_level,
        ]),
        evaluate=lambda: invoke(["evaluate", "--database", str(database)]),
        paper_simulate=lambda: invoke(["paper-simulate", "--database", str(database)]),
        daily_report=lambda: _daily_report(database, None, notifier),
        auto_research=lambda: invoke([
            "auto-research", "--database", str(database),
            "--sectors-config", str(args.sectors_config),
            "--baseline-config", str(args.baseline_config),
            "--step-bars", str(args.research_step_bars),
        ]),
        collect_history=lambda: invoke([
            "collect-history", "--database", str(database), "--config", str(args.config),
            "--sectors-config", str(args.sectors_config), "--base-url", args.base_url,
            "--days", str(args.history_days), "--timeout", str(args.timeout),
            "--max-retries", str(args.max_retries),
            "--request-pause", str(args.history_request_pause),
            "--log-level", args.log_level,
        ]),
        hotlist_alert=(
            (lambda: _run_hotlist_alert_task(args, notifier))
            if args.enable_hotlist_alerts else None
        ),
        history_interval=timedelta(hours=args.history_interval_hours),
    )
    repository = MarketDataRepository(database)
    try:
        runner = ProductionRunner(
            repository, tasks, lock_path, poll_seconds=args.poll_seconds, observer=observe_task
        )
        try:
            runner.run_forever(once=args.once)
        except RunnerLockError as error:
            print(json.dumps({"status": "LOCKED", "error": str(error)}, separators=(",", ":")))
            return 3
        except KeyboardInterrupt:
            return 0
    finally:
        repository.close()
    return 0


def _health(database: Path) -> int:
    repository = MarketDataRepository(database)
    try:
        payload = HealthService(repository, database).snapshot()
    finally:
        repository.close()
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0 if payload["sqlite"]["healthy"] else 2


def _ops(args: argparse.Namespace) -> int:
    if args.ops_command == "status":
        configured = bool(
            os.environ.get("TELEGRAM_BOT_TOKEN")
            and os.environ.get("TELEGRAM_CHAT_ID")
        )
        payload = build_ops_status(args.database, configured)
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return 0 if payload["database_health"]["healthy"] else 2
    if args.ops_command == "daily":
        report = render_ops_daily(args.database, args.baseline_config)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        print(
            json.dumps(
                {"status": "SUCCEEDED", "report": str(args.report), "research_only": True},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    payload = run_safety_audit(Path.cwd(), args.baseline_config)
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0 if payload["status"] == "PASS" else 2


def _telegram(args: argparse.Namespace) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(
            json.dumps(
                {
                    "status": "SKIPPED",
                    "skipped_reason": "telegram_not_configured",
                    "research_only": True,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    now = datetime.now(UTC)
    plan = HotlistEntryPlan(
        symbol="BTCUSDT",
        direction="LONG",
        current_price=Decimal("100"),
        change_24h_pct=Decimal("15"),
        quote_volume=Decimal("5000000"),
        volume_ratio_15m=Decimal("1.5"),
        ema20_15m=Decimal("99"),
        atr14=Decimal("2"),
        swing_high=Decimal("105"),
        swing_low=Decimal("95"),
        suggested_limit_entry=Decimal("99"),
        stop_loss=Decimal("96"),
        tp1=Decimal("102"),
        tp2=Decimal("105"),
        rr=Decimal("2"),
        expires_at=(now + timedelta(hours=1)).isoformat(timespec="seconds"),
        reason="Operational Telegram test using a sample research-only plan.",
    )
    alert = HotlistAlert(
        symbol=plan.symbol,
        direction=plan.direction,
        entry=plan.suggested_limit_entry,
        created_at=now.isoformat(timespec="seconds"),
        level="MEDIUM",
        plan=plan,
    )
    TelegramNotifier(token, chat_id, args.telegram_timeout).send(
        format_hotlist_alert_message(alert)
    )
    print(
        json.dumps(
            {"status": "SENT", "symbol": alert.symbol, "research_only": True},
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _comparison_json(comparison: object) -> dict[str, object]:
    metrics = comparison.metrics
    return {
        "strategy_id": comparison.strategy_id,
        "trades": metrics.total_signals,
        "win_rate": metrics.tp2_win_rate,
        "profit_factor": metrics.profit_factor,
        "expectancy": metrics.expectancy_r,
        "max_drawdown": metrics.max_drawdown_r,
        "regime_breakdown": _metrics_breakdown_json(comparison.regime_breakdown),
        "direction_breakdown": _metrics_breakdown_json(comparison.direction_breakdown),
    }


def _ranking_json(ranking: object) -> dict[str, object]:
    payload = _comparison_json(ranking)
    return {
        "rank": ranking.rank,
        **payload,
        "verdict": ranking.verdict,
    }


def _sweep_json(result: object) -> dict[str, object]:
    metrics = result.metrics
    return {
        "rank": result.rank,
        "parameters": result.parameters,
        "trades": metrics.total_signals,
        "win_rate": metrics.tp2_win_rate,
        "profit_factor": metrics.profit_factor,
        "expectancy": metrics.expectancy_r,
        "max_drawdown": metrics.max_drawdown_r,
        "verdict": result.verdict,
    }


def _champion_json(standing: object) -> dict[str, object]:
    metrics = standing.metrics
    return {
        "rank": standing.rank,
        "strategy_id": standing.strategy_id,
        "score": standing.score,
        "profit_factor": metrics.profit_factor,
        "expectancy": metrics.expectancy_r,
        "max_drawdown": metrics.max_drawdown_r,
        "trade_count": metrics.total_signals,
        "verdict": standing.verdict,
    }


def _metrics_breakdown_json(breakdown: object) -> dict[str, object]:
    return {
        name: {
            "trades": metrics.total_signals,
            "win_rate": metrics.tp2_win_rate,
            "profit_factor": metrics.profit_factor,
            "expectancy": metrics.expectancy_r,
            "max_drawdown": metrics.max_drawdown_r,
        }
        for name, metrics in breakdown.items()
    }


def _strategy_version_json(version: object) -> dict[str, object]:
    return {
        "strategy_id": version.strategy_id,
        "name": version.name,
        "description": version.description,
        "status": version.status,
        "created_at": version.created_at,
        "config": version.config.as_dict(),
        "metrics": asdict(version.metrics) if version.metrics is not None else None,
    }


def _evaluate(database: Path) -> int:
    repository = MarketDataRepository(database)
    try:
        summary = SignalEvaluator(repository).evaluate_all()
    finally:
        repository.close()
    print(
        json.dumps(
            {
                "total_signals": summary.total_signals,
                "win_tp2_count": summary.win_tp2_count,
                "tp1_hit_count": summary.tp1_hit_count,
                "loss_count": summary.loss_count,
                "expired_count": summary.expired_count,
                "tp1_hit_rate": summary.tp1_hit_rate,
                "tp2_win_rate": summary.tp2_win_rate,
                "loss_rate": summary.loss_rate,
                "expired_rate": summary.expired_rate,
                "expectancy_r": summary.expectancy_r,
                "average_max_favorable_pct": summary.average_max_favorable_pct,
                "average_max_adverse_pct": summary.average_max_adverse_pct,
                "by_direction": {
                    key: asdict(value) for key, value in summary.by_direction.items()
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _ai_macro(args: argparse.Namespace) -> int:
    if args.ai_macro_command == "scan":
        return _ai_macro_scan(args)
    if args.ai_macro_command == "review":
        return _ai_macro_review(args)
    if args.ai_macro_command == "settle":
        return _ai_macro_settle(args)
    if args.ai_macro_command == "performance":
        return _ai_macro_performance(args)
    return 1


def _ai_macro_scan(args: argparse.Namespace) -> int:
    import uuid
    from datetime import UTC, datetime

    from binance_ai_trader.ai_macro import (
        AIMacroRepository,
        AIMacroTrade,
        MacroAnalyzer,
        MIN_SCORE,
        calculate_performance,
        format_ai_macro_scan_message,
        render_ai_macro_report,
        score_candidate,
    )

    client = BinancePublicClient(
        args.base_url,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    repository = AIMacroRepository(args.database)
    now = datetime.now(UTC).astimezone(UTC)

    try:
        all_tickers = {t.symbol: t for t in client.tickers_24h()}
        btc = all_tickers.get("BTCUSDT")
        eth = all_tickers.get("ETHUSDT")
        if btc is None or eth is None:
            print(json.dumps({"error": "BTC or ETH ticker unavailable"}, separators=(",", ":")))
            return 1

        analysis = MacroAnalyzer().analyze(btc, eth, now)

        if analysis.trade_bias == "NO_TRADE":
            payload = {
                "generated_at": analysis.generated_at,
                "market_state": analysis.market_state,
                "risk_grade": analysis.risk_grade,
                "trade_bias": analysis.trade_bias,
                "new_trades": [],
                "reason": "RISK_OFF: no new virtual trades",
            }
            print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            return 0

        universe = UniverseConfig.load(args.config)
        valid = {
            t.symbol
            for t in all_tickers.values()
            if t.symbol.endswith("USDT")
            and t.symbol not in universe.denied_symbols
        }
        pool = [
            t for t in all_tickers.values()
            if t.symbol in valid and t.symbol not in {"BTCUSDT", "ETHUSDT"}
        ]
        gainers = sorted(
            [t for t in pool if t.price_change_percent > 0],
            key=lambda t: (-t.price_change_percent, -t.quote_volume),
        )[: args.gainers]
        losers = sorted(
            [t for t in pool if t.price_change_percent < 0],
            key=lambda t: (t.price_change_percent, -t.quote_volume),
        )[: args.losers]

        if analysis.trade_bias == "LONG_ONLY":
            candidates = [(t, "LONG") for t in gainers]
        elif analysis.trade_bias == "SHORT_ONLY":
            candidates = [(t, "SHORT") for t in losers]
        else:
            candidates = [(t, "LONG") for t in gainers] + [(t, "SHORT") for t in losers]

        scores = []
        for ticker, direction in candidates:
            klines_15m = client.klines(ticker.symbol, "15m", limit=60)
            scored = score_candidate(ticker.symbol, direction, ticker, klines_15m, now)
            scores.append(scored)

        open_count = repository.open_count()
        new_trades: list[AIMacroTrade] = []
        for scored in sorted(scores, key=lambda s: -s.score):
            if scored.direction == "PASS":
                continue
            if open_count >= 5:
                break
            if scored.entry is None or scored.stop_loss is None or scored.tp1 is None or scored.tp2 is None:
                continue
            trade = AIMacroTrade(
                trade_id=str(uuid.uuid4())[:8],
                created_at=now.isoformat(timespec="seconds"),
                symbol=scored.symbol,
                direction=scored.direction,
                entry=scored.entry,
                stop_loss=scored.stop_loss,
                tp1=scored.tp1,
                tp2=scored.tp2,
                score=scored.score,
                market_state=analysis.market_state,
                risk_grade=analysis.risk_grade,
                reason=scored.reason,
                status="OPEN",
                pnl_pct=None,
                closed_at=None,
            )
            repository.save_trade(trade)
            new_trades.append(trade)
            open_count += 1

        skipped = sum(1 for s in scores if s.direction == "PASS")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            render_ai_macro_report(analysis, scores, new_trades, skipped),
            encoding="utf-8",
        )

        telegram_status: dict[str, str] = {}
        if getattr(args, "send_telegram", False):
            notifier = _telegram_notifier(args)
            if notifier:
                notifier.send(format_ai_macro_scan_message(analysis, new_trades))
                telegram_status = {"telegram": "SENT"}
            else:
                telegram_status = {"telegram": "SKIPPED"}

        print(json.dumps(
            {
                "generated_at": analysis.generated_at,
                "market_state": analysis.market_state,
                "risk_grade": analysis.risk_grade,
                "trade_bias": analysis.trade_bias,
                "btc_change_pct": str(analysis.btc_change_pct),
                "eth_change_pct": str(analysis.eth_change_pct),
                "candidates_scored": len(scores),
                "new_trades": [
                    {
                        "trade_id": t.trade_id,
                        "symbol": t.symbol,
                        "direction": t.direction,
                        "score": t.score,
                        "entry": str(t.entry),
                        "stop_loss": str(t.stop_loss),
                        "tp1": str(t.tp1),
                        "tp2": str(t.tp2),
                    }
                    for t in new_trades
                ],
                "report": str(args.report),
                **telegram_status,
            },
            separators=(",", ":"),
            sort_keys=True,
        ))
        return 0
    finally:
        repository.close()


def _ai_macro_review(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal as _Decimal

    from binance_ai_trader.ai_macro import (
        AIMacroRepository,
        format_ai_macro_review_message,
    )

    client = BinancePublicClient(
        args.base_url,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    repository = AIMacroRepository(args.database)
    now = datetime.now(UTC).astimezone(UTC)

    try:
        open_trades = list(repository.open_trades())
        if not open_trades:
            print(json.dumps({"open_trades": 0, "updated": []}, separators=(",", ":")))
            return 0

        all_tickers = {t.symbol: t for t in client.tickers_24h()}
        updated_ids: list[str] = []

        for trade in open_trades:
            if trade.symbol not in all_tickers:
                continue
            klines = client.klines(trade.symbol, "15m", limit=2)
            if not klines:
                continue
            current_price = klines[-1].close

            if trade.direction == "LONG":
                if current_price >= trade.tp2:
                    pnl = (trade.tp2 - trade.entry) / trade.entry * _Decimal("100")
                    repository.update_trade(trade.trade_id, "TP2", pnl.quantize(_Decimal("0.01")), now.isoformat(timespec="seconds"))
                    updated_ids.append(trade.trade_id)
                elif current_price >= trade.tp1:
                    pnl = (trade.tp1 - trade.entry) / trade.entry * _Decimal("100")
                    repository.update_trade(trade.trade_id, "TP1", pnl.quantize(_Decimal("0.01")), now.isoformat(timespec="seconds"))
                    updated_ids.append(trade.trade_id)
                elif current_price <= trade.stop_loss:
                    pnl = (trade.stop_loss - trade.entry) / trade.entry * _Decimal("100")
                    repository.update_trade(trade.trade_id, "STOP", pnl.quantize(_Decimal("0.01")), now.isoformat(timespec="seconds"))
                    updated_ids.append(trade.trade_id)
            else:
                if current_price <= trade.tp2:
                    pnl = (trade.entry - trade.tp2) / trade.entry * _Decimal("100")
                    repository.update_trade(trade.trade_id, "TP2", pnl.quantize(_Decimal("0.01")), now.isoformat(timespec="seconds"))
                    updated_ids.append(trade.trade_id)
                elif current_price <= trade.tp1:
                    pnl = (trade.entry - trade.tp1) / trade.entry * _Decimal("100")
                    repository.update_trade(trade.trade_id, "TP1", pnl.quantize(_Decimal("0.01")), now.isoformat(timespec="seconds"))
                    updated_ids.append(trade.trade_id)
                elif current_price >= trade.stop_loss:
                    pnl = (trade.entry - trade.stop_loss) / trade.entry * _Decimal("100")
                    repository.update_trade(trade.trade_id, "STOP", pnl.quantize(_Decimal("0.01")), now.isoformat(timespec="seconds"))
                    updated_ids.append(trade.trade_id)

        current_prices = {}
        for trade in open_trades:
            klines = client.klines(trade.symbol, "15m", limit=2)
            if klines:
                current_prices[trade.symbol] = klines[-1].close

        telegram_status: dict[str, str] = {}
        if getattr(args, "send_telegram", False):
            notifier = _telegram_notifier(args)
            if notifier:
                msg = format_ai_macro_review_message(
                    open_trades,
                    current_prices,
                    now.isoformat(timespec="seconds"),
                )
                notifier.send(msg)
                telegram_status = {"telegram": "SENT"}
            else:
                telegram_status = {"telegram": "SKIPPED"}

        print(json.dumps(
            {
                "reviewed_at": now.isoformat(timespec="seconds"),
                "open_trades": len(open_trades),
                "updated": updated_ids,
                **telegram_status,
            },
            separators=(",", ":"),
            sort_keys=True,
        ))
        return 0
    finally:
        repository.close()


def _ai_macro_settle(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal as _Decimal

    from binance_ai_trader.ai_macro import (
        AIMacroRepository,
        AIMacroTrade,
        format_ai_macro_settle_message,
    )

    client = BinancePublicClient(
        args.base_url,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    repository = AIMacroRepository(args.database)
    now = datetime.now(UTC).astimezone(UTC)
    expiry_cutoff = now - timedelta(hours=48)

    try:
        open_trades = list(repository.open_trades())
        expired: list[AIMacroTrade] = []

        for trade in open_trades:
            try:
                created = datetime.fromisoformat(trade.created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
            except ValueError:
                continue
            if created > expiry_cutoff:
                continue

            klines = client.klines(trade.symbol, "15m", limit=2)
            if klines:
                current_price = klines[-1].close
            else:
                current_price = trade.entry

            if trade.direction == "LONG":
                pnl = (current_price - trade.entry) / trade.entry * _Decimal("100")
            else:
                pnl = (trade.entry - current_price) / trade.entry * _Decimal("100")

            pnl = pnl.quantize(_Decimal("0.01"))
            repository.update_trade(
                trade.trade_id, "EXPIRED", pnl, now.isoformat(timespec="seconds")
            )
            from dataclasses import replace as _replace
            closed_trade = AIMacroTrade(
                trade_id=trade.trade_id,
                created_at=trade.created_at,
                symbol=trade.symbol,
                direction=trade.direction,
                entry=trade.entry,
                stop_loss=trade.stop_loss,
                tp1=trade.tp1,
                tp2=trade.tp2,
                score=trade.score,
                market_state=trade.market_state,
                risk_grade=trade.risk_grade,
                reason=trade.reason,
                status="EXPIRED",
                pnl_pct=pnl,
                closed_at=now.isoformat(timespec="seconds"),
            )
            expired.append(closed_trade)

        telegram_status: dict[str, str] = {}
        if getattr(args, "send_telegram", False) and expired:
            notifier = _telegram_notifier(args)
            if notifier:
                notifier.send(format_ai_macro_settle_message(expired))
                telegram_status = {"telegram": "SENT"}
            else:
                telegram_status = {"telegram": "SKIPPED"}

        print(json.dumps(
            {
                "settled_at": now.isoformat(timespec="seconds"),
                "expired_count": len(expired),
                "expired": [
                    {
                        "trade_id": t.trade_id,
                        "symbol": t.symbol,
                        "direction": t.direction,
                        "pnl_pct": str(t.pnl_pct),
                    }
                    for t in expired
                ],
                **telegram_status,
            },
            separators=(",", ":"),
            sort_keys=True,
        ))
        return 0
    finally:
        repository.close()


def _ai_macro_performance(args: argparse.Namespace) -> int:
    from binance_ai_trader.ai_macro import (
        AIMacroRepository,
        calculate_performance,
        format_ai_macro_performance_message,
        render_ai_macro_performance,
    )

    repository = AIMacroRepository(args.database)
    try:
        all_trades = repository.all_trades()
        perf = calculate_performance(all_trades)

        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_ai_macro_performance(perf), encoding="utf-8")

        telegram_status: dict[str, str] = {}
        if getattr(args, "send_telegram", False):
            notifier = _telegram_notifier(args)
            if notifier:
                notifier.send(format_ai_macro_performance_message(perf))
                telegram_status = {"telegram": "SENT"}
            else:
                telegram_status = {"telegram": "SKIPPED"}

        print(json.dumps(
            {
                "total_trades": perf.total_trades,
                "open_trades": perf.open_trades,
                "closed_trades": perf.closed_trades,
                "win_count": perf.win_count,
                "tp1_count": perf.tp1_count,
                "tp2_count": perf.tp2_count,
                "stop_count": perf.stop_count,
                "expired_count": perf.expired_count,
                "win_rate": str(perf.win_rate),
                "tp1_rate": str(perf.tp1_rate),
                "tp2_rate": str(perf.tp2_rate),
                "avg_pnl_pct": str(perf.avg_pnl_pct),
                "virtual_balance": str(perf.virtual_balance),
                "report": str(args.report),
                **telegram_status,
            },
            separators=(",", ":"),
            sort_keys=True,
        ))
        return 0
    finally:
        repository.close()


def _gemini_committee(args: argparse.Namespace) -> int:
    import json
    import os

    from binance_ai_trader.gemini_committee.committee import GeminiCommittee

    bot_token = getattr(args, "telegram_bot_token", None) or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(args, "telegram_chat_id", None) or os.environ.get("TELEGRAM_CHAT_ID", "")

    gc = GeminiCommittee(
        db_path=str(args.database),
        ai_macro_db_path=str(args.ai_macro_database),
        model=args.gemini_model,
        max_candidates=args.max_candidates,
        cooldown_hours=args.cooldown_hours,
        base_url=args.base_url,
        gemini_timeout=args.gemini_timeout,
        gemini_retries=args.gemini_retries,
    )
    try:
        result = gc.review(
            send_telegram=args.send_telegram,
            telegram_bot_token=bot_token,
            telegram_chat_id=chat_id,
            telegram_timeout=getattr(args, "telegram_timeout", 10.0),
        )
    finally:
        gc.close()

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
