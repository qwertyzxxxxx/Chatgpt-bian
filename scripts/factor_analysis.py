"""
因子分析：對 793 筆歷史訂單重算入場時的量能指標，找出與勝率相關的因子閾值。

分析的因子：
  1. vol_ratio_15m   — 入場15m蠟燭量比（current / avg前20根）
  2. vol_ratio_1h    — 入場1h蠟燭量比
  3. vol_ratio_4h    — 入場4h蠟燭量比
  4. ema_dist_pct    — 入場價距離15m EMA20的百分比距離
  5. atr_ratio       — 入場蠟燭實體 / ATR14（波動強度）

輸出：
  - 每個因子按分箱的勝率分布表（找甜蜜區間）
  - 多因子組合勝率矩陣（vol_ratio_15m × vol_ratio_1h）

運行：
    python scripts/factor_analysis.py
    python scripts/factor_analysis.py --strategy v66   # 只看某策略
    python scripts/factor_analysis.py --direction LONG
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.request
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import NamedTuple

# ─── Binance API ──────────────────────────────────────────────────────────────

BASE_URL = "https://fapi.binance.com"
_cache: dict = {}
_cache_lock = threading.Lock()


def _ascii_safe(symbol: str) -> bool:
    try:
        symbol.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def fetch_klines(symbol: str, interval: str, end_ms: int, limit: int = 60) -> list[dict]:
    """
    拉 Binance Futures K 線（舊→新）。
    返回 [{"open","high","low","close","volume","quote_volume"}, ...]
    """
    if not _ascii_safe(symbol):
        return []
    # 緩存 key：同一根蠟燭起點共用
    interval_ms = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}[interval]
    candle_slot = end_ms // interval_ms
    key = (symbol, interval, candle_slot)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    url = (f"{BASE_URL}/fapi/v1/klines"
           f"?symbol={symbol}&interval={interval}&limit={limit}&endTime={end_ms}")
    req = urllib.request.Request(url, headers={"User-Agent": "factor-analysis/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read())
        result = [
            {
                "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]),  "close": float(r[4]),
                "volume": float(r[5]), "quote_volume": float(r[7]),
            }
            for r in rows
        ]
        with _cache_lock:
            _cache[key] = result
        return result
    except Exception as e:
        print(f"  [WARN] {symbol} {interval}: {e}", file=sys.stderr)
        with _cache_lock:
            _cache[key] = []   # 緩存失敗結果，避免重試
        return []


# ─── 技術指標計算 ──────────────────────────────────────────────────────────────

def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    val = sum(values[:period]) / period
    for v in values[period:]:
        val = v * k + val * (1 - k)
    return val


def atr14(klines: list[dict]) -> float | None:
    """ATR14：使用 True Range。"""
    if len(klines) < 15:
        return None
    trs = []
    for i in range(1, len(klines)):
        prev_close = klines[i - 1]["close"]
        h, l, c = klines[i]["high"], klines[i]["low"], prev_close
        trs.append(max(h - l, abs(h - c), abs(l - c)))
    # Wilder smoothing seed
    if len(trs) < 14:
        return None
    val = sum(trs[:14]) / 14
    for tr in trs[14:]:
        val = (val * 13 + tr) / 14
    return val


def volume_ratio(klines: list[dict]) -> float | None:
    """
    同策略定義：recent = 前20根（-21:-1），current = 最後一根。
    ratio = current.quote_volume / avg(recent)
    """
    if len(klines) < 22:
        return None
    recent = klines[-21:-1]
    avg = sum(k["quote_volume"] for k in recent) / len(recent)
    if avg <= 0:
        return None
    return klines[-1]["quote_volume"] / avg


# ─── 每筆訂單計算因子 ──────────────────────────────────────────────────────────

class OrderFeatures(NamedTuple):
    strategy: str
    direction: str
    result: str      # TP1 or SL
    vol_ratio_15m: float | None
    vol_ratio_1h: float | None
    vol_ratio_4h: float | None
    ema_dist_pct: float | None   # (entry - ema20) / ema20 * 100
    atr_ratio: float | None      # 最後一根實體 / ATR14


def compute_features(order: dict) -> OrderFeatures | None:
    symbol    = order["symbol"]
    direction = order["direction"]
    result    = order["result"]
    strategy  = order["strategy_id"].replace("hotlist_", "")
    filled_at = order["filled_at"]

    if not _ascii_safe(symbol):
        return None

    # 解析時間
    dt = datetime.fromisoformat(str(filled_at).replace("Z", "+00:00"))
    filled_ms = int(dt.timestamp() * 1000)

    # 拉三個時框 K 線（並行化：先構建所有 URL，再依序拉；Python 單線程用緩存就夠）
    k15 = fetch_klines(symbol, "15m", filled_ms, limit=60)
    k1h = fetch_klines(symbol, "1h",  filled_ms, limit=60)
    k4h = fetch_klines(symbol, "4h",  filled_ms, limit=60)

    if len(k15) < 22:
        return None

    # ── 量比 ──
    vr15 = volume_ratio(k15)
    vr1h = volume_ratio(k1h) if len(k1h) >= 22 else None
    vr4h = volume_ratio(k4h) if len(k4h) >= 22 else None

    # ── EMA20 距離 ──
    closes_15m = [k["close"] for k in k15]
    e20 = ema(closes_15m, 20)
    entry_price = float(order.get("entry", 0) or 0)
    ema_dist = None
    if e20 and entry_price > 0:
        ema_dist = (entry_price - e20) / e20 * 100   # 正=入場在EMA上方

    # ── ATR 實體比 ──
    at = atr14(k15)
    last = k15[-1]
    body = abs(last["close"] - last["open"])
    atr_r = body / at if at and at > 0 else None

    return OrderFeatures(
        strategy=strategy,
        direction=direction,
        result=result,
        vol_ratio_15m=vr15,
        vol_ratio_1h=vr1h,
        vol_ratio_4h=vr4h,
        ema_dist_pct=ema_dist,
        atr_ratio=atr_r,
    )


# ─── 分析輸出 ──────────────────────────────────────────────────────────────────

def bin_label(val: float, edges: list[float]) -> str:
    for i, edge in enumerate(edges[:-1]):
        if val < edges[i + 1]:
            lo = f"{edge:.1f}" if edge != int(edge) else str(int(edge))
            hi = f"{edges[i+1]:.1f}" if edges[i+1] != int(edges[i+1]) else str(int(edges[i+1]))
            return f"[{lo},{hi})"
    lo = f"{edges[-1]:.1f}" if edges[-1] != int(edges[-1]) else str(int(edges[-1]))
    return f"[{lo},∞)"


def winrate_table(
    features: list[OrderFeatures],
    attr: str,
    edges: list[float],
    title: str,
    strategy_filter: str | None,
    direction_filter: str | None,
) -> None:
    """按某因子分箱，打印勝率表。"""
    # group: (strategy, direction, bin) → [win, total]
    groups: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])

    for f in features:
        if strategy_filter and f.strategy != strategy_filter:
            continue
        if direction_filter and f.direction != direction_filter:
            continue
        val = getattr(f, attr)
        if val is None:
            continue
        label = bin_label(val, edges)
        key = (f.strategy, f.direction, label)
        groups[key][1] += 1
        if f.result == "TP1":
            groups[key][0] += 1

    if not groups:
        print(f"  （無數據）")
        return

    # 收集所有 bins
    all_bins = sorted({k[2] for k in groups})

    # 按策略+方向列印
    current_sd = None
    rows_by_sd: dict[tuple, dict] = defaultdict(dict)
    for (strat, direction, b), (w, t) in groups.items():
        rows_by_sd[(strat, direction)][b] = (w, t)

    print(f"\n{'─'*70}")
    print(f"  {title}")
    print(f"{'─'*70}")
    header = f"  {'策略+方向':<18}" + "".join(f"{b:>12}" for b in all_bins)
    print(header)
    print(f"{'─'*70}")

    for (strat, direction) in sorted(rows_by_sd.keys()):
        row = rows_by_sd[(strat, direction)]
        line = f"  {strat+' '+direction:<18}"
        for b in all_bins:
            if b in row:
                w, t = row[b]
                pct = f"{100*w/t:.0f}%({t})" if t >= 3 else f"~({t})"
            else:
                pct = "—"
            line += f"{pct:>12}"
        print(line)


def matrix_table(
    features: list[OrderFeatures],
    attr_x: str, edges_x: list[float], label_x: str,
    attr_y: str, edges_y: list[float], label_y: str,
    strategy_filter: str | None,
    direction_filter: str | None,
) -> None:
    """二維因子矩陣：勝率熱圖。"""
    bins_x = []
    bins_y = []
    groups: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])

    for f in features:
        if strategy_filter and f.strategy != strategy_filter:
            continue
        if direction_filter and f.direction != direction_filter:
            continue
        vx = getattr(f, attr_x)
        vy = getattr(f, attr_y)
        if vx is None or vy is None:
            continue
        bx = bin_label(vx, edges_x)
        by = bin_label(vy, edges_y)
        key = (bx, by)
        groups[key][1] += 1
        if f.result == "TP1":
            groups[key][0] += 1

    all_bx = sorted({k[0] for k in groups})
    all_by = sorted({k[1] for k in groups})

    if not groups:
        print("  （數據不足）")
        return

    print(f"\n{'─'*70}")
    print(f"  矩陣：{label_x} × {label_y}  （勝率%，括號內為樣本數）")
    print(f"{'─'*70}")
    header = f"  {label_x+'↓'+label_y+'→':<14}" + "".join(f"{b:>14}" for b in all_by)
    print(header)
    print(f"{'─'*70}")
    for bx in all_bx:
        line = f"  {bx:<14}"
        for by in all_by:
            wt = groups.get((bx, by), [0, 0])
            w, t = wt
            if t >= 3:
                cell = f"{100*w/t:.0f}%({t})"
            elif t > 0:
                cell = f"~({t})"
            else:
                cell = "—"
            line += f"{cell:>14}"
        print(line)


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",       default="/tmp/orders_raw.csv")
    parser.add_argument("--strategy",  default=None, help="v66 / v662 / v663 / v664")
    parser.add_argument("--direction", default=None, help="LONG / SHORT")
    args = parser.parse_args()

    # 讀訂單
    orders = []
    with open(args.csv) as f:
        for row in csv.DictReader(f):
            if row["result"] not in ("TP1", "SL"):
                continue
            orders.append(row)
    print(f"[*] 訂單數：{len(orders)}")

    # 計算因子（多線程並行，20 workers）
    features: list[OrderFeatures] = []
    skipped = 0
    done_count = [0]
    lock = threading.Lock()

    def process_order(order):
        feat = compute_features(order)
        with lock:
            done_count[0] += 1
            if done_count[0] % 100 == 0:
                print(f"  ... {done_count[0]}/{len(orders)}", flush=True)
        return feat

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(process_order, o) for o in orders]
        for fut in as_completed(futures):
            feat = fut.result()
            if feat is None:
                skipped += 1
            else:
                features.append(feat)

    print(f"\n[*] 有效因子：{len(features)} 筆，跳過 {skipped} 筆\n")

    sf = args.strategy
    df = args.direction

    # ══ 1. vol_ratio_15m ══
    edges_vol = [0, 0.3, 0.6, 1.0, 1.5, 2.0, 3.0]
    winrate_table(features, "vol_ratio_15m", edges_vol,
                  "① 15m 量比（入場蠟燭 / 前20根均量）", sf, df)

    # ══ 2. vol_ratio_1h ══
    winrate_table(features, "vol_ratio_1h", edges_vol,
                  "② 1h 量比", sf, df)

    # ══ 3. vol_ratio_4h ══
    winrate_table(features, "vol_ratio_4h", edges_vol,
                  "③ 4h 量比", sf, df)

    # ══ 4. EMA 距離 ══
    edges_dist = [-5, -2, -1, -0.5, 0, 0.5, 1, 2, 5]
    winrate_table(features, "ema_dist_pct", edges_dist,
                  "④ 入場價距15m EMA20的距離%（負=在EMA下方，正=上方）", sf, df)

    # ══ 5. ATR 實體比 ══
    edges_atr = [0, 0.2, 0.4, 0.7, 1.0, 1.5]
    winrate_table(features, "atr_ratio", edges_atr,
                  "⑤ 入場蠟燭實體/ATR14（大=當前蠟燭強勢）", sf, df)

    # ══ 6. 二維矩陣：15m量比 × 1h量比 ══
    print(f"\n\n{'═'*70}")
    print("  多因子組合分析")
    print(f"{'═'*70}")

    edges_vol_m = [0, 0.5, 1.0, 1.5, 3.0]
    matrix_table(features,
                 "vol_ratio_15m", edges_vol_m, "15m量比",
                 "vol_ratio_1h",  edges_vol_m, "1h量比",
                 sf, df)

    # ══ 7. 找最優單因子閾值 ══
    print(f"\n\n{'═'*70}")
    print("  最優閾值搜索（min樣本數=15，嘗試 vol_ratio_15m 所有切點）")
    print(f"{'═'*70}")

    for strategy in sorted({f.strategy for f in features}):
        for direction in ["LONG", "SHORT"]:
            subset = [f for f in features
                      if f.strategy == strategy and f.direction == direction
                      and f.vol_ratio_15m is not None]
            if len(subset) < 20:
                continue
            # 掃描閾值 0.1 ~ 3.0
            best_above = (0, 0, 0)   # threshold, win_pct, sample
            best_below = (0, 0, 0)
            for thr_int in range(1, 30):
                thr = thr_int / 10.0
                above = [f for f in subset if f.vol_ratio_15m >= thr]
                below = [f for f in subset if f.vol_ratio_15m < thr]
                if len(above) >= 15:
                    wp = sum(1 for f in above if f.result == "TP1") / len(above)
                    if wp > best_above[1]:
                        best_above = (thr, wp, len(above))
                if len(below) >= 15:
                    wp = sum(1 for f in below if f.result == "TP1") / len(below)
                    if wp > best_below[1]:
                        best_below = (thr, wp, len(below))
            all_wp = sum(1 for f in subset if f.result == "TP1") / len(subset)
            print(f"\n  {strategy} {direction}  整體勝率={all_wp*100:.1f}% ({len(subset)}筆)")
            if best_above[0]:
                t, wp, n = best_above
                print(f"    最佳「量比 >= {t:.1f}」→ {wp*100:.1f}% ({n}筆)  {'↑提升' if wp>all_wp else '↓下降'}{abs(wp-all_wp)*100:.1f}%")
            if best_below[0]:
                t, wp, n = best_below
                print(f"    最佳「量比 < {t:.1f}」→ {wp*100:.1f}% ({n}筆)  {'↑提升' if wp>all_wp else '↓下降'}{abs(wp-all_wp)*100:.1f}%")


if __name__ == "__main__":
    main()
