from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from dataclasses import asdict
from pathlib import Path

from binance_ai_trader.application.analyze_market_regime import MarketRegimeAnalyzer
from binance_ai_trader.application.analyze_sector_strength import SectorStrengthAnalyzer
from binance_ai_trader.application.collect_market_data import MarketDataCollector
from binance_ai_trader.application.evaluate_signals import SignalEvaluator
from binance_ai_trader.application.generate_signals import SignalGenerator
from binance_ai_trader.application.score_market_data import MarketScorer
from binance_ai_trader.backtest import BacktestEngine, BacktestPolicy
from binance_ai_trader.config import SectorConfig, UniverseConfig
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.paper.service import PaperSimulator
from binance_ai_trader.reporting import DailyReportService
from binance_ai_trader.runner import HealthService, ProductionRunner, RunnerLockError, default_tasks
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.strategy_lab.service import StrategyLab


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Binance USD-M Futures read-only analysis")
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    run_loop.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    health = subparsers.add_parser("health", help="print runner and database health JSON")
    health.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    health.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    evaluate = subparsers.add_parser("evaluate", help="evaluate stored LONG/SHORT signals")
    evaluate.add_argument("--database", type=Path, default=Path("data/market_data.db"))
    evaluate.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"scan", "regime", "sectors", "backtest", "evaluate", "strategies", "auto-research", "auto_research", "paper-simulate", "daily-report", "run-loop", "health", "-h", "--help"}:
        arguments.insert(0, "scan")
    args = build_parser().parse_args(arguments)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "evaluate":
        return _evaluate(args.database)
    if args.command == "run-loop":
        return _run_loop(args)
    if args.command == "health":
        return _health(args.database)
    if args.command == "strategies":
        return _strategies(args)
    if args.command in {"auto-research", "auto_research"}:
        return _auto_research(args)
    if args.command == "paper-simulate":
        return _paper_simulate(args.database)
    if args.command == "daily-report":
        return _daily_report(args.database, args.date)
    if args.command == "backtest":
        return _backtest(args)
    if args.command == "regime":
        return _regime(args.database)
    if args.command == "sectors":
        return _sectors(args.database, args.config)
    return _scan(args)


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
        market_regime = MarketRegimeAnalyzer(repository).analyze()
        failed_symbols = {failure.split("/", 1)[0] for failure in result.failed_requests}
        scoring_result = MarketScorer(repository).score_run(
            run_id=result.run_id,
            symbols=(member.symbol for member in result.universe),
            excluded_symbols=failed_symbols,
        )
        SectorStrengthAnalyzer(repository, sector_map).analyze_latest()
        signal_result = SignalGenerator(repository, sector_map=sector_map).generate_latest()
    finally:
        repository.close()

    for signal in signal_result.signals:
        print(
            json.dumps(
                {
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "score": signal.score,
                    "combined_regime": signal.combined_regime,
                    "sector": signal.sector,
                    "sector_rank": signal.sector_rank,
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
                },
                separators=(",", ":"),
            )
        )
    return 0


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


def _daily_report(database: Path, report_date: date | None) -> int:
    repository = MarketDataRepository(database)
    try:
        payload = DailyReportService(repository).build(report_date)
    finally:
        repository.close()
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


def _run_loop(args: argparse.Namespace) -> int:
    database = args.database
    lock_path = args.lock_file or Path(f"{database}.runner.lock")

    def invoke(arguments: list[str]) -> int:
        return main(arguments)

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
        daily_report=lambda: invoke(["daily-report", "--database", str(database)]),
        auto_research=lambda: invoke([
            "auto-research", "--database", str(database),
            "--sectors-config", str(args.sectors_config),
            "--baseline-config", str(args.baseline_config),
            "--step-bars", str(args.research_step_bars),
        ]),
    )
    repository = MarketDataRepository(database)
    try:
        runner = ProductionRunner(
            repository, tasks, lock_path, poll_seconds=args.poll_seconds
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
    return 0


def _comparison_json(comparison: object) -> dict[str, object]:
    metrics = comparison.metrics
    return {
        "strategy_id": comparison.strategy_id,
        "total_signals": metrics.total_signals,
        "tp1_hit_rate": metrics.tp1_hit_rate,
        "tp2_win_rate": metrics.tp2_win_rate,
        "loss_rate": metrics.loss_rate,
        "profit_factor": metrics.profit_factor,
        "expectancy_r": metrics.expectancy_r,
        "max_drawdown_r": metrics.max_drawdown_r,
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


if __name__ == "__main__":
    raise SystemExit(main())
