"""
回測分析：對現有歷史訂單套用「4h Triple EMA 過濾」，看勝率變化。

流程：
1. 從生產 DB 拉 hotlist_v66/v662/v663/v664 已成交訂單（TP1 or SL）
2. 每筆訂單 → 用 filled_at 時間拉 4h K線（endTime=filled_at）
3. 算 EMA10/EMA20/EMA50 on close
4. 判斷是否 4h Triple EMA 對齊（多：EMA10>EMA20>EMA50；空：反之）
5. 對比 "4h對齊" vs "4h不對齊" 的勝率差異

運行：
    python scripts/backtest_4h_filter.py

也可加 1h 副過濾：
    python scripts/backtest_4h_filter.py --with-1h
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import json
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from collections import defaultdict

# ─── EMA 計算 ────────────────────────────────────────────────────────────────

def ema(closes: list[float], period: int) -> float | None:
    """指數移動平均。closes 從舊到新排列。"""
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    val = sum(closes[:period]) / period   # SMA seed
    for c in closes[period:]:
        val = c * k + val * (1 - k)
    return val


def triple_ema_aligned(closes: list[float], direction: str) -> bool | None:
    """
    判斷 EMA10/20/50 是否三線排列對齊。
    closes: 50+ 根 K 線收盤價，舊→新。
    返回 None 表示數據不足。
    """
    if len(closes) < 60:
        return None
    e10 = ema(closes, 10)
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    if e10 is None or e20 is None or e50 is None:
        return None
    if direction == "LONG":
        return e10 > e20 > e50
    else:
        return e10 < e20 < e50


# ─── Binance 公開 API ─────────────────────────────────────────────────────────

BASE_URL = "https://fapi.binance.com"
_kline_cache: dict[tuple[str, str, int], list[float]] = {}   # (symbol, interval, end_ts_sec) → closes


def fetch_closes(symbol: str, interval: str, end_ms: int, limit: int = 80) -> list[float]:
    """拉 Binance Futures K 線 close 列表（舊→新）。帶簡單緩存。"""
    # 含非 ASCII 字符的 symbol（如中文）Binance 不支援，直接跳過
    try:
        symbol.encode("ascii")
    except UnicodeEncodeError:
        return []

    cache_key = (symbol, interval, end_ms // (4 * 3600 * 1000))   # 同一根 4h 蠟燭共用緩存
    if cache_key in _kline_cache:
        return _kline_cache[cache_key]

    url = (f"{BASE_URL}/fapi/v1/klines"
           f"?symbol={symbol}&interval={interval}&limit={limit}&endTime={end_ms}")
    req = urllib.request.Request(url, headers={"User-Agent": "backtest-script/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        closes = [float(row[4]) for row in data]
        _kline_cache[cache_key] = closes
        return closes
    except Exception as e:
        print(f"  [WARN] fetch_closes {symbol} {interval}: {e}", file=sys.stderr)
        return []


# ─── 從 DB 拉訂單 ──────────────────────────────────────────────────────────────

def load_orders_from_db() -> list[dict]:
    """用 psycopg2 從本地/生產 DB 拉訂單。"""
    import psycopg2
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        SELECT order_id, strategy_id, symbol, direction, result,
               filled_at, closed_at, pnl_pct, entry, stop_loss
        FROM v3_paper_orders
        WHERE strategy_id IN ('hotlist_v66','hotlist_v662','hotlist_v663','hotlist_v664')
          AND result IN ('TP1','SL')
          AND filled_at IS NOT NULL
        ORDER BY filled_at
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    print(f"[DB] Loaded {len(rows)} orders from database")
    return rows


def load_orders_from_csv(path: str) -> list[dict]:
    """備用：從 CSV 讀取（executeSql 匯出的格式）。"""
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    print(f"[CSV] Loaded {len(rows)} orders from {path}")
    return rows


# ─── 主分析 ───────────────────────────────────────────────────────────────────

def analyse(orders: list[dict], with_1h: bool = False) -> None:
    total = len(orders)
    print(f"\n{'='*60}")
    print(f"  訂單總數: {total}")
    print(f"  策略: hotlist_v66 / v662 / v663 / v664")
    print(f"  副過濾: {'4h + 1h Triple EMA' if with_1h else '僅 4h Triple EMA'}")
    print(f"{'='*60}\n")

    # 分組統計結構: group → {wins, total, skipped}
    # group = (strategy_id, direction, aligned_4h)
    stats: dict[tuple, dict] = defaultdict(lambda: {"wins": 0, "total": 0, "skipped": 0})
    # 總覽
    overall: dict[str, dict] = defaultdict(lambda: {"aligned_w": 0, "aligned_t": 0,
                                                     "unaligned_w": 0, "unaligned_t": 0,
                                                     "skip": 0})

    for i, order in enumerate(orders):
        symbol    = order["symbol"]
        direction = order["direction"]
        result    = order["result"]   # TP1 or SL
        filled_at = order["filled_at"]
        strategy  = order["strategy_id"]

        # 解析時間 → ms
        if isinstance(filled_at, datetime):
            filled_ms = int(filled_at.timestamp() * 1000)
        else:
            # ISO string, e.g. "2026-07-07T19:14:26+00:00"
            dt = datetime.fromisoformat(str(filled_at).replace("Z", "+00:00"))
            filled_ms = int(dt.timestamp() * 1000)

        # 拉 4h K 線（最多 80 根，覆蓋至少 60 根用於 EMA50）
        closes_4h = fetch_closes(symbol, "4h", filled_ms, limit=80)
        aligned_4h = triple_ema_aligned(closes_4h, direction)

        aligned = aligned_4h   # 先用 4h

        if with_1h and aligned_4h:
            closes_1h = fetch_closes(symbol, "1h", filled_ms, limit=80)
            aligned_1h = triple_ema_aligned(closes_1h, direction)
            aligned = aligned_4h and aligned_1h

        is_win = (result == "TP1")
        key = (strategy, direction, aligned)

        if aligned is None:
            stats[key]["skipped"] += 1
            overall[strategy]["skip"] += 1
            continue

        stats[key]["total"] += 1
        if is_win:
            stats[key]["wins"] += 1

        if aligned:
            overall[strategy]["aligned_t"] += 1
            if is_win:
                overall[strategy]["aligned_w"] += 1
        else:
            overall[strategy]["unaligned_t"] += 1
            if is_win:
                overall[strategy]["unaligned_w"] += 1

        # 節流：Binance 公開 API 無需 key，但每秒避免過快
        if i % 20 == 19:
            time.sleep(0.3)
            if i % 100 == 99:
                print(f"  ...processed {i+1}/{total}", flush=True)

    # ─── 列印詳細結果 ─────────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  {'策略':<20} {'方向':<6} {'4h對齊':<8} {'勝':<5} {'總':<5} {'勝率':>6}")
    print(f"{'─'*70}")

    # aligned 可能是 None（數據不足跳過），排序時轉為字符串
    def sort_key(item):
        (strat, direction, aligned), _ = item
        return (strat, direction, str(aligned))

    for (strat, direction, aligned), d in sorted(stats.items(), key=sort_key):
        t = d["total"]
        w = d["wins"]
        s = d["skipped"]
        pct = f"{100*w/t:.1f}%" if t else "—"
        aligned_str = "✅ 對齊" if aligned else "❌ 不對"
        skip_str    = f"  (跳過 {s})" if s else ""
        strat_short = strat.replace("hotlist_", "")
        print(f"  {strat_short:<20} {direction:<6} {aligned_str:<10} {w:<5} {t:<5} {pct:>6}{skip_str}")

    # ─── 策略彙總 ─────────────────────────────────────────────────────────────
    print(f"\n{'═'*70}")
    print(f"  {'策略':<20} {'4h對齊勝率':>10} {'4h對齊單數':>10} {'未對齊勝率':>10} {'未對齊單數':>10} {'過濾率':>8}")
    print(f"{'═'*70}")
    for strat, d in sorted(overall.items()):
        at = d["aligned_t"]; aw = d["aligned_w"]
        ut = d["unaligned_t"]; uw = d["unaligned_w"]
        total_t = at + ut
        filter_rate = f"{100*(1-at/total_t):.0f}%" if total_t else "—"
        a_pct = f"{100*aw/at:.1f}%" if at else "—"
        u_pct = f"{100*uw/ut:.1f}%" if ut else "—"
        strat_short = strat.replace("hotlist_", "")
        print(f"  {strat_short:<20} {a_pct:>10} {at:>10} {u_pct:>10} {ut:>10} {filter_rate:>8}")

    print(f"\n結論：")
    print(f"  - 如果 4h 對齊的勝率 >> 不對齊，說明加 4h 過濾有效")
    print(f"  - 過濾率 = 加 4h 後會損失多少信號量")
    print(f"  - 理想：對齊勝率 ≥ 70%，過濾率 ≤ 60%（信號還夠）")


# ─── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="4h Triple EMA 過濾回測分析")
    parser.add_argument("--with-1h", action="store_true", help="同時要求 1h Triple EMA 也對齊")
    parser.add_argument("--csv",     default=None,        help="CSV 路徑（不指定則從 DB 讀）")
    args = parser.parse_args()

    if args.csv:
        orders = load_orders_from_csv(args.csv)
    else:
        try:
            orders = load_orders_from_db()
        except Exception as e:
            print(f"[WARN] DB failed ({e}), trying CSV fallback /tmp/orders_raw.csv")
            orders = load_orders_from_csv("/tmp/orders_raw.csv")

    analyse(orders, with_1h=args.with_1h)


if __name__ == "__main__":
    main()
