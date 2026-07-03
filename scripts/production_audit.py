"""
Production Hotlist Unique Order Audit
======================================
粘贴到生产 VM Shell 运行：
    python3 scripts/production_audit.py

只读打开 data/market_data.db，不写入任何数据。
结果保存到 /tmp/production_hotlist_*.csv 和 /tmp/production_hotlist_validation.md
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

DB_PATH = Path("data/market_data.db")
DEDUP_TOLERANCE = Decimal("0.002")   # ±0.2%
EXPIRY_HOURS = 24
MAX_WORKERS = 20
FAPI_BASE = "https://fapi.binance.com"

# ─── helpers ──────────────────────────────────────────────────────────────────

def ts_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

def dt_to_ts(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)

def parse_dt(s: str) -> datetime:
    s = s.replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch 15m klines from Binance FAPI covering [start_ms, end_ms]."""
    results = []
    cur = start_ms
    limit = 500
    while cur < end_ms:
        url = (
            f"{FAPI_BASE}/fapi/v1/klines"
            f"?symbol={symbol}&interval=15m"
            f"&startTime={cur}&endTime={end_ms}&limit={limit}"
        )
        for attempt in range(3):
            try:
                with urlopen(url, timeout=15) as r:
                    data = json.loads(r.read())
                break
            except Exception:
                if attempt == 2:
                    return results
                time.sleep(1)
        if not data:
            break
        for row in data:
            results.append({
                "open_time": row[0],
                "open":  Decimal(str(row[1])),
                "high":  Decimal(str(row[2])),
                "low":   Decimal(str(row[3])),
                "close": Decimal(str(row[4])),
                "close_time": row[6],
            })
        cur = data[-1][6] + 1
        if len(data) < limit:
            break
    return results

# ─── step 1: load production DB ───────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"DB path: {DB_PATH.resolve()}")
if not DB_PATH.exists():
    print("ERROR: DB not found. Are you in the project root?")
    sys.exit(1)

con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

def count(table: str, where: str = "") -> int:
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        return con.execute(sql).fetchone()[0]
    except Exception:
        return -1

n_opps     = count("hotlist_opportunities")
n_alerts   = count("hotlist_alerts")
n_outcomes = count("hotlist_outcomes")
n_strat    = count("strategy_results", "strategy='hotlist'")

print(f"\n{'─'*40}")
print("STEP 1 — Production Table Counts")
print(f"{'─'*40}")
print(f"hotlist_opportunities             : {n_opps:>6}")
print(f"hotlist_alerts                    : {n_alerts:>6}")
print(f"hotlist_outcomes                  : {n_outcomes:>6}")
print(f"strategy_results (hotlist)        : {n_strat:>6}")
print(f"{'─'*40}")
print("Source: READ-ONLY query of data/market_data.db on THIS VM")

# ─── step 2: load opportunities & dedup ───────────────────────────────────────

rows = con.execute(
    "SELECT id, symbol, direction, entry, sl, tp1, tp2, rr, confidence, created_at "
    "FROM hotlist_opportunities ORDER BY created_at ASC, id ASC"
).fetchall()
con.close()

all_opps = []
for r in rows:
    all_opps.append({
        "id":         r["id"],
        "symbol":     r["symbol"],
        "direction":  r["direction"],
        "entry":      Decimal(r["entry"]),
        "sl":         Decimal(r["sl"]),
        "tp1":        Decimal(r["tp1"]),
        "tp2":        Decimal(r["tp2"]),
        "rr":         r["rr"],
        "confidence": r["confidence"],
        "created_at": r["created_at"],
    })

original_count = len(all_opps)

# cluster-dedup: (symbol, direction) + entry within ±0.2%
# earliest created_at = representative
groups: list[list[dict]] = []

for opp in all_opps:
    placed = False
    for group in groups:
        rep = group[0]
        if rep["symbol"] != opp["symbol"]:
            continue
        if rep["direction"] != opp["direction"]:
            continue
        ratio = abs(rep["entry"] - opp["entry"]) / rep["entry"]
        if ratio <= DEDUP_TOLERANCE:
            group.append(opp)
            placed = True
            break
    if not placed:
        groups.append([opp])

unique_orders = [g[0] for g in groups]
unique_count  = len(unique_orders)
dup_rate      = (original_count - unique_count) / original_count * 100

print(f"\n{'─'*40}")
print("STEP 2 — Deduplication (symbol+direction+entry±0.2%)")
print(f"{'─'*40}")
print(f"Original Opportunities : {original_count}")
print(f"Unique Orders          : {unique_count}")
print(f"Duplicate Rate         : {dup_rate:.1f}%")

# Top 30 most duplicated symbols
dup_count: dict[str, int] = {}
for g in groups:
    key = f"{g[0]['symbol']}_{g[0]['direction']}"
    dup_count[key] = len(g)

top30 = sorted(dup_count.items(), key=lambda x: -x[1])[:30]

print(f"\nTop 30 most duplicated (symbol_direction → count):")
for k, v in top30:
    print(f"  {k:<30} {v:>4} times")

# ─── step 3: fetch klines & settle ────────────────────────────────────────────

print(f"\n{'─'*40}")
print("STEP 3 — Binance Historical Kline Re-settlement (24h)")
print(f"{'─'*40}")
print(f"Fetching klines for {unique_count} unique orders …")

results_map: dict[int, dict] = {}

def settle_order(opp: dict) -> dict:
    created_at = parse_dt(opp["created_at"])
    window_end  = created_at + timedelta(hours=EXPIRY_HOURS)
    start_ms   = dt_to_ts(created_at)
    end_ms     = dt_to_ts(window_end + timedelta(hours=EXPIRY_HOURS))  # fetch extra buffer

    klines = fetch_klines(opp["symbol"], start_ms, end_ms)

    entry = opp["entry"]
    sl    = opp["sl"]
    tp1   = opp["tp1"]
    direction = opp["direction"]

    # Phase 1: find fill
    fill_time = None
    fill_candle_idx = None
    for i, k in enumerate(klines):
        if k["open_time"] < start_ms:
            continue
        if k["open_time"] > dt_to_ts(window_end):
            break
        if direction == "LONG" and k["low"] <= entry:
            fill_time = ts_to_dt(k["open_time"])
            fill_candle_idx = i
            break
        if direction == "SHORT" and k["high"] >= entry:
            fill_time = ts_to_dt(k["open_time"])
            fill_candle_idx = i
            break

    if fill_time is None:
        return {**opp, "status": "EXPIRED_NOT_FILLED",
                "fill_time": None, "exit_time": None,
                "wait_minutes": None, "hold_minutes": None, "pnl_pct": None}

    wait_minutes = (fill_time - created_at).total_seconds() / 60
    hold_end = fill_time + timedelta(hours=EXPIRY_HOURS)

    # Phase 2: settle TP1 / SL within 24h of fill
    status = "TIMEOUT"
    exit_time = hold_end
    exit_price = entry

    for k in klines[fill_candle_idx:]:
        if k["open_time"] > dt_to_ts(hold_end):
            break
        if direction == "LONG":
            if k["low"] <= sl:
                status = "SL"; exit_price = sl; exit_time = ts_to_dt(k["open_time"]); break
            if k["high"] >= tp1:
                status = "TP1"; exit_price = tp1; exit_time = ts_to_dt(k["open_time"]); break
        else:
            if k["high"] >= sl:
                status = "SL"; exit_price = sl; exit_time = ts_to_dt(k["open_time"]); break
            if k["low"] <= tp1:
                status = "TP1"; exit_price = tp1; exit_time = ts_to_dt(k["open_time"]); break

    if direction == "LONG":
        pnl = (exit_price - entry) / entry * 100
    else:
        pnl = (entry - exit_price) / entry * 100

    hold_minutes = (exit_time - fill_time).total_seconds() / 60

    return {**opp, "status": status,
            "fill_time": fill_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "wait_minutes": wait_minutes,
            "hold_minutes": hold_minutes,
            "pnl_pct": float(pnl)}

settled_results: list[dict] = []
done = 0
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
    futures = {ex.submit(settle_order, opp): opp for opp in unique_orders}
    for fut in as_completed(futures):
        done += 1
        if done % 100 == 0 or done == unique_count:
            print(f"  {done}/{unique_count} done …", flush=True)
        settled_results.append(fut.result())

# ─── step 4: aggregate stats ──────────────────────────────────────────────────

filled      = [r for r in settled_results if r["status"] != "EXPIRED_NOT_FILLED"]
expired     = [r for r in settled_results if r["status"] == "EXPIRED_NOT_FILLED"]
timeout     = [r for r in settled_results if r["status"] == "TIMEOUT"]
tp1_list    = [r for r in settled_results if r["status"] == "TP1"]
sl_list     = [r for r in settled_results if r["status"] == "SL"]
settled     = tp1_list + sl_list   # decisive

fill_rate   = len(filled) / unique_count * 100
win_rate    = len(tp1_list) / len(settled) * 100 if settled else 0.0

longs  = [r for r in settled if r["direction"] == "LONG"]
shorts = [r for r in settled if r["direction"] == "SHORT"]
long_wins  = [r for r in longs  if r["status"] == "TP1"]
short_wins = [r for r in shorts if r["status"] == "TP1"]
long_wr  = len(long_wins)  / len(longs)  * 100 if longs  else 0.0
short_wr = len(short_wins) / len(shorts) * 100 if shorts else 0.0

def avg(lst): return sum(lst) / len(lst) if lst else 0.0

avg_pnl   = avg([r["pnl_pct"]    for r in settled if r["pnl_pct"] is not None])
avg_wait  = avg([r["wait_minutes"] for r in filled if r["wait_minutes"] is not None])
avg_hold  = avg([r["hold_minutes"] for r in settled if r["hold_minutes"] is not None])

print(f"\n{'─'*40}")
print("STEP 4 — Final Statistics")
print(f"{'─'*40}")
print(f"Original Opportunities : {original_count}")
print(f"Unique Orders          : {unique_count}")
print(f"Duplicate Rate         : {dup_rate:.1f}%")
print(f"Filled                 : {len(filled)}")
print(f"Fill Rate              : {fill_rate:.1f}%")
print(f"Expired (not filled)   : {len(expired)}")
print(f"Timeout                : {len(timeout)}")
print(f"TP1                    : {len(tp1_list)}")
print(f"SL                     : {len(sl_list)}")
print(f"Settled (TP1+SL)       : {len(settled)}")
print(f"Win Rate (TP1/Settled) : {win_rate:.1f}%")
print(f"Average PnL (settled)  : {avg_pnl:+.2f}%")
print(f"Average Wait Minutes   : {avg_wait:.1f}")
print(f"Average Hold Minutes   : {avg_hold:.1f}")
print(f"LONG  Win Rate         : {long_wr:.1f}%  ({len(long_wins)}/{len(longs)})")
print(f"SHORT Win Rate         : {short_wr:.1f}%  ({len(short_wins)}/{len(shorts)})")

# ─── step 5: save files ───────────────────────────────────────────────────────

out_orders = Path("/tmp/production_hotlist_unique_orders.csv")
out_results = Path("/tmp/production_hotlist_unique_results.csv")
out_md     = Path("/tmp/production_hotlist_validation.md")

# unique orders CSV
order_fields = ["id","symbol","direction","entry","sl","tp1","tp2","rr","confidence","created_at"]
with open(out_orders, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=order_fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(unique_orders)

# results CSV
result_fields = ["id","symbol","direction","entry","sl","tp1","created_at",
                 "status","fill_time","exit_time","wait_minutes","hold_minutes","pnl_pct"]
with open(out_results, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=result_fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(settled_results)

# markdown report
generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
md = f"""# Production Hotlist Unique Order Audit

**Generated**: {generated_at}
**DB**: `data/market_data.db` (read-only, production VM)
**Settlement**: 24h expiry · TP1/SL/Timeout · 15m klines

---

## Table Counts

| Table | Count |
|---|---:|
| hotlist_opportunities | {n_opps:,} |
| hotlist_alerts | {n_alerts:,} |
| hotlist_outcomes | {n_outcomes:,} |
| strategy_results (hotlist) | {n_strat:,} |

---

## Deduplication

| | Count |
|---|---:|
| Original Opportunities | {original_count:,} |
| Unique Orders | {unique_count:,} |
| Duplicate Rate | {dup_rate:.1f}% |

**Dedup rule**: symbol + direction + entry (±0.2%), earliest created_at = representative.

---

## Settlement Results

| Metric | Value |
|---|---:|
| Filled | {len(filled):,} |
| Fill Rate | {fill_rate:.1f}% |
| Expired (not filled) | {len(expired):,} |
| Timeout | {len(timeout):,} |
| TP1 | {len(tp1_list):,} |
| SL | {len(sl_list):,} |
| Settled (TP1+SL) | {len(settled):,} |
| **Win Rate (TP1/Settled)** | **{win_rate:.1f}%** |
| Average PnL (settled) | {avg_pnl:+.2f}% |
| Average Wait Minutes | {avg_wait:.1f} |
| Average Hold Minutes | {avg_hold:.1f} |
| LONG Win Rate | {long_wr:.1f}% ({len(long_wins)}/{len(longs)}) |
| SHORT Win Rate | {short_wr:.1f}% ({len(short_wins)}/{len(shorts)}) |

---

## Top 30 Most Duplicated

| Rank | Symbol_Direction | Count |
|---|---|---:|
"""
for i, (k, v) in enumerate(top30, 1):
    md += f"| {i} | {k} | {v} |\n"

md += f"""
---

## Final Answer

1. **Unique Orders**: {unique_count:,}
2. **Settled (TP1+SL)**: {len(settled):,}
3. **Win Rate**: {win_rate:.1f}%
"""

out_md.write_text(md)

print(f"\nFiles saved:")
print(f"  {out_orders}")
print(f"  {out_results}")
print(f"  {out_md}")

# ─── step 6: FINAL block ──────────────────────────────────────────────────────

print(f"""
======== FINAL ========

Original Opportunities : {original_count}
Unique Orders          : {unique_count}
Filled                 : {len(filled)}
Settled (TP1+SL)       : {len(settled)}
TP1                    : {len(tp1_list)}
SL                     : {len(sl_list)}
Win Rate               : {win_rate:.1f}%
Fill Rate              : {fill_rate:.1f}%
Average PnL            : {avg_pnl:+.2f}%

======================
""")
