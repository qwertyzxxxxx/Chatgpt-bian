import logging
from market_hunter.database import db

logger = logging.getLogger(__name__)


def _pct_str(val) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


def print_report():
    """Print a summary report of recent signals and their evaluations."""
    db.init_db()

    print("\n" + "=" * 70)
    print("  MARKET HUNTER — US SCAN REPORT")
    print("=" * 70)

    # Recent scan runs
    runs = db.get_scan_runs(limit=5)
    if runs:
        print("\n📅 Recent Scan Runs:")
        print(f"  {'Date':<12} {'Scanned':>8} {'Signals':>8} {'Duration':>10} {'Status':<10}")
        print("  " + "-" * 55)
        for r in runs:
            print(
                f"  {r['run_date']:<12} {r['total_scanned'] or 0:>8} "
                f"{r['total_signals'] or 0:>8} "
                f"{r['duration_seconds'] or 0:>9.1f}s  {r['status']:<10}"
            )

    # Recent signals with evaluations
    signals = db.get_recent_signals(limit=50)
    if not signals:
        print("\n  No signals found yet. Run: python main.py scan-us\n")
        return

    evaluated = [s for s in signals if s.get("return_5d") is not None]
    pending = [s for s in signals if s.get("return_5d") is None]

    if evaluated:
        print(f"\n📊 Evaluated Signals ({len(evaluated)}):")
        print(
            f"  {'Symbol':<8} {'Date':<12} {'Score':>6} {'5d':>8} {'10d':>8} {'20d':>8} "
            f"{'MaxDD':>8} {'MaxGain':>9} {'Sector':<20}"
        )
        print("  " + "-" * 90)
        for s in evaluated[:30]:
            print(
                f"  {s['symbol']:<8} {s['signal_date']:<12} {s['total_score']:>6.1f} "
                f"{_pct_str(s.get('return_5d')):>8} {_pct_str(s.get('return_10d')):>8} "
                f"{_pct_str(s.get('return_20d')):>8} {_pct_str(s.get('max_drawdown')):>8} "
                f"{_pct_str(s.get('max_gain')):>9}  {(s.get('sector') or '')[:20]:<20}"
            )

        # Summary stats
        ret5 = [s["return_5d"] for s in evaluated if s["return_5d"] is not None]
        ret10 = [s["return_10d"] for s in evaluated if s["return_10d"] is not None]
        ret20 = [s["return_20d"] for s in evaluated if s["return_20d"] is not None]

        def avg(lst):
            return sum(lst) / len(lst) if lst else None

        def win_rate(lst):
            return sum(1 for x in lst if x > 0) / len(lst) * 100 if lst else None

        print("\n  📈 Average Returns:")
        print(f"    5d:  avg={_pct_str(avg(ret5))}  win_rate={win_rate(ret5):.0f}%" if ret5 else "    5d:  N/A")
        print(f"    10d: avg={_pct_str(avg(ret10))}  win_rate={win_rate(ret10):.0f}%" if ret10 else "    10d: N/A")
        print(f"    20d: avg={_pct_str(avg(ret20))}  win_rate={win_rate(ret20):.0f}%" if ret20 else "    20d: N/A")

    if pending:
        print(f"\n⏳ Pending Evaluation ({len(pending)} signals — need 5+ trading days):")
        print(f"  {'Symbol':<8} {'Date':<12} {'Score':>6} {'Price':>8} {'Strategies'}")
        print("  " + "-" * 60)
        for s in pending[:20]:
            print(
                f"  {s['symbol']:<8} {s['signal_date']:<12} {s['total_score']:>6.1f} "
                f"${s['close_price']:>7.2f}  {s.get('strategies', '')}"
            )

    print("\n" + "=" * 70 + "\n")
