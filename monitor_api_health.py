"""Binance API Health Monitor — read-only, no code modification.
Usage:
    python3 monitor_api_health.py            # last 24h report
    python3 monitor_api_health.py --hours 1  # last 1h report
"""
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta

DB = "data/market_data.db"
TASKS = ["scan", "hotlist_alert", "hotlist_performance"]


def classify(msg: str) -> tuple[int, int, int, int, int]:
    m = (msg or "").lower()
    is418 = "418" in m
    is429 = "429" in m
    istmo = "timeout" in m
    iscon = "connection" in m or "remote end" in m or "eof" in m
    isoth = not (is418 or is429 or istmo or iscon)
    return int(is418), int(is429), int(istmo), int(iscon), int(isoth)


def report(hours: int = 24) -> None:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row

    print(f"\n{'='*54}")
    print(f"  Binance API Health Report")
    print(f"  Generated : {now_str}")
    print(f"  Window    : last {hours}h  (since {since[:16]} UTC)")
    print(f"{'='*54}")

    tot_ok = tot_fail = c418 = c429 = ctmo = ccon = coth = 0

    for task in TASKS:
        rows = con.execute(
            """SELECT status, error_message, started_at
               FROM runner_events
               WHERE event_type=? AND started_at >= ?
               ORDER BY started_at""",
            (task, since),
        ).fetchall()

        ok = sum(1 for r in rows if r["status"] == "SUCCEEDED")
        fails = [r for r in rows if r["status"] == "FAILED"]
        f418 = f429 = ftmo = fcon = foth = 0
        for r in fails:
            a, b, c, d, e = classify(r["error_message"])
            f418 += a; f429 += b; ftmo += c; fcon += d; foth += e

        tot_ok += ok; tot_fail += len(fails)
        c418 += f418; c429 += f429; ctmo += ftmo; ccon += fcon; coth += foth

        total_task = ok + len(fails)
        rate = f"{ok/total_task*100:.0f}%" if total_task else "N/A"
        print(f"\n  [{task}]")
        print(f"    runs={total_task}  ok={ok}  fail={len(fails)}  rate={rate}")
        if fails:
            print(f"    418={f418}  429={f429}  Timeout={ftmo}  Conn={fcon}  Other={foth}")
            for r in fails[-3:]:
                ts = r["started_at"][:16]
                err = str(r["error_message"] or "")[:72]
                print(f"    ✗ {ts}  {err}")

    total = tot_ok + tot_fail
    rate = f"{tot_ok/total*100:.1f}%" if total else "N/A"

    print(f"\n{'─'*54}")
    print(f"  总请求  : {total}")
    print(f"  成功    : {tot_ok}")
    print(f"  失败    : {tot_fail}")
    print(f"  HTTP 418: {c418}")
    print(f"  HTTP 429: {c429}")
    print(f"  Timeout : {ctmo}")
    print(f"  ConnErr : {ccon}")
    print(f"  其它    : {coth}")
    print(f"  成功率  : {rate}")
    print(f"{'─'*54}")

    if c418 > 30:
        print(f"  🔴 CRITICAL — 418 超过 30 次/{hours}h")
    elif c418 > 10:
        print(f"  🟡 WARNING  — 418 超过 10 次/{hours}h")
    else:
        print(f"  🟢 NORMAL")
    print(f"{'='*54}\n")

    con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    report(args.hours)
