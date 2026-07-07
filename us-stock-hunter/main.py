#!/usr/bin/env python3
"""
market-hunter — US Stock Daily Scanner + Paper Fund
Usage:
  python main.py scan-us          Run the US stock scan now
  python main.py evaluate-us      Evaluate historical signal returns
  python main.py report-us        Print the signal performance report
  python main.py schedule         Start the daily scheduler (blocks)
  python main.py performance      Strategy performance analytics
  python main.py sector-report    Sector analytics report
  python main.py best-signals     Top 20 best trades ever
  python main.py quality-report   Signal quality / actionability report
  python main.py readiness        Generate PRODUCTION_READINESS.md
  python main.py paper-init       Initialize paper fund ($100,000)
  python main.py paper-daily      Run paper fund daily (no scan)
  python main.py paper-report     Send paper fund Telegram report
"""
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("market_hunter")


def cmd_scan():
    from market_hunter.scanner import run_us_scan
    print("\n🔍 Running US stock scan...\n")
    results = run_us_scan(notify=True)
    if not results:
        print("❌ Scan failed. Check logs for details.")
        return

    dr = results.get("diagnostics_report", {})
    print(f"\n✅ Scan complete in {results['duration_seconds']}s")
    print(f"   Scanned: {results['total_scanned']} stocks")
    print(f"   Valid price: {results['summary']['valid_price_count']}  "
          f"Rejected: {results['summary']['rejected_bad_price_count']}")
    print(f"   Signals: {results['total_signals']} total  "
          f"(MA60={dr.get('ma60_count',0)} "
          f"Trend={dr.get('strong_trend_count',0)} "
          f"NH={dr.get('new_high_count',0)})\n")

    print("🏆 Top 20 by Score:")
    for i, s in enumerate(results["top20"], 1):
        strats = ", ".join(s.get("strategies", []))
        strat_str = f" [{strats}]" if strats else ""
        print(f"  {i:2}. {s['symbol']:<7} Score:{s['total_score']:5.1f}  "
              f"${s['close_price']:.2f}  {s.get('sector', '')[:25]}{strat_str}")

    print("\n📐 Top 5 MA60 Reclaim Pullback:")
    for i, s in enumerate(results["ma60_reclaim"], 1):
        ep = s.get("entry_plan") or {}
        status = ep.get("action_status", "")
        flag = "✅" if status == "可关注买点" else ("❗" if "风险" in status else "⚠️")
        print(f"  {i}. {s['symbol']:<7} Score:{s['total_score']:5.1f}  "
              f"${s['close_price']:.2f}  {flag}")

    print("\n📈 Top 5 Strong Trend Pullback:")
    for i, s in enumerate(results["strong_trend"], 1):
        ep = s.get("entry_plan") or {}
        status = ep.get("action_status", "")
        flag = "✅" if status == "可关注买点" else ("❗" if "风险" in status else "⚠️")
        print(f"  {i}. {s['symbol']:<7} Score:{s['total_score']:5.1f}  "
              f"${s['close_price']:.2f}  {flag}")

    print("\n🚀 Top 5 New High Breakout:")
    for i, s in enumerate(results["new_high"], 1):
        ep = s.get("entry_plan") or {}
        status = ep.get("action_status", "")
        flag = "✅" if status == "可关注买点" else ("❗" if "风险" in status else "⚠️")
        print(f"  {i}. {s['symbol']:<7} Score:{s['total_score']:5.1f}  "
              f"${s['close_price']:.2f}  {flag}")

    print()


def cmd_evaluate():
    from market_hunter.evaluator import run_evaluation
    print("\n📊 Evaluating historical signals...\n")
    result = run_evaluation()
    print(f"✅ Done: {result['evaluated']} evaluated, {result['skipped']} skipped "
          f"(out of {result['total']} pending)\n")


def cmd_report():
    from market_hunter.report import print_report
    print_report()


def cmd_schedule():
    from market_hunter.scheduler.runner import start_scheduler
    from market_hunter.config import SCHEDULER_HOUR_MY, SCHEDULER_MINUTE_MY, SCHEDULER_TZ
    print(f"\n⏰ Starting scheduler — US scan runs daily at "
          f"{SCHEDULER_HOUR_MY:02d}:{SCHEDULER_MINUTE_MY:02d} {SCHEDULER_TZ}")
    print("Press Ctrl+C to stop.\n")
    start_scheduler()


# ─── Analytics commands ───────────────────────────────────────────────────────

def cmd_performance():
    """Part 1 + 2: per-strategy metrics and ranking."""
    from market_hunter.database.db import init_db
    from market_hunter.analytics import get_strategy_stats, print_performance_report
    init_db()
    stats = get_strategy_stats()
    print_performance_report(stats)


def cmd_sector_report():
    """Part 3: sector-level performance."""
    from market_hunter.database.db import init_db
    from market_hunter.analytics import get_sector_stats, print_sector_report
    init_db()
    rows = get_sector_stats()
    print_sector_report(rows)


def cmd_best_signals():
    """Part 4: top 20 best trades ever."""
    from market_hunter.database.db import init_db
    from market_hunter.analytics import get_best_signals, print_best_signals
    init_db()
    rows = get_best_signals(limit=20)
    print_best_signals(rows)


def cmd_quality_report():
    """Part 5: signal quality / actionability."""
    from market_hunter.database.db import init_db
    from market_hunter.analytics import get_quality_stats, print_quality_report
    init_db()
    q = get_quality_stats()
    print_quality_report(q)


def cmd_readiness():
    """Part 6: run all analytics and generate PRODUCTION_READINESS.md."""
    from market_hunter.database.db import init_db
    from market_hunter.analytics import (
        get_strategy_stats, get_sector_stats, get_quality_stats,
        print_performance_report, print_sector_report, print_quality_report,
        generate_readiness_md,
    )
    init_db()

    print("\n📊 Running all analytics...\n")
    strategy_stats = get_strategy_stats()
    sector_stats   = get_sector_stats()
    quality        = get_quality_stats()

    print_performance_report(strategy_stats)
    print_sector_report(sector_stats)
    print_quality_report(quality)

    md = generate_readiness_md(strategy_stats, quality, sector_stats)
    path = "PRODUCTION_READINESS.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"✅ PRODUCTION_READINESS.md written to {path}\n")


# ─── Paper Fund commands ──────────────────────────────────────────────────────

def cmd_paper_init():
    """Initialize paper fund with $100,000. Safe to call multiple times."""
    from market_hunter.paper_fund.fund import init_fund
    fund = init_fund()
    print(f"\n✅ Paper Fund initialized")
    print(f"   Initial capital : ${fund['initial_capital']:,.2f}")
    print(f"   Current cash    : ${fund['current_cash']:,.2f}")
    print(f"   Created         : {fund.get('created_at', 'N/A')}\n")


def cmd_paper_daily():
    """Run paper fund daily processing without a live scan (uses DB state only)."""
    from datetime import date
    from market_hunter.paper_fund.fund import run_daily
    scan_date = date.today().isoformat()
    print(f"\n📂 Running paper fund daily (standalone) — {scan_date}\n")
    activity = run_daily(scan_date, scan_results=None, notify=True)
    if "error" in activity:
        print(f"❌ {activity['error']}")
        return
    eq = activity.get("equity") or {}
    print(f"✅ Done")
    print(f"   Total equity    : ${eq.get('total_equity', 0):,.2f}")
    print(f"   Cash            : ${eq.get('cash', 0):,.2f}")
    print(f"   Position value  : ${eq.get('position_value', 0):,.2f}")
    print(f"   Cumulative PnL  : {eq.get('pnl_cumulative_pct', 0):+.1f}%")
    print(f"   Open positions  : {len(activity.get('positions', []))}")
    print(f"   Pending orders  : {len(activity.get('pending', []))}\n")


def cmd_paper_report():
    """Send paper fund Telegram report from current DB state."""
    from datetime import date
    from market_hunter.paper_fund.reporter import send_paper_report_standalone
    scan_date = date.today().isoformat()
    print(f"\n📤 Sending paper fund report — {scan_date}\n")
    ok = send_paper_report_standalone(scan_date)
    print("✅ Report sent\n" if ok else "❌ Report failed — check Telegram config\n")


# ─── Dispatch ─────────────────────────────────────────────────────────────────

COMMANDS = {
    "scan-us":        cmd_scan,
    "evaluate-us":    cmd_evaluate,
    "report-us":      cmd_report,
    "schedule":       cmd_schedule,
    "performance":    cmd_performance,
    "sector-report":  cmd_sector_report,
    "best-signals":   cmd_best_signals,
    "quality-report": cmd_quality_report,
    "readiness":      cmd_readiness,
    "paper-init":     cmd_paper_init,
    "paper-daily":    cmd_paper_daily,
    "paper-report":   cmd_paper_report,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("Available commands:")
        for cmd in COMMANDS:
            print(f"  python main.py {cmd}")
        sys.exit(1)

    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
