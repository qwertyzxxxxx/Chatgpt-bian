"""Classic C1-C4 + K1-K4 + K3v2/K4v2 master scanner.

Single cycle:
  1. Build EXTENDED universe (top-40 gainers + top-40 losers, 30M filter)
  2. Main pool (top-20): run C1-C4, K1-K4 existing strategies
  3. Extended pool (21-40): fetch klines only (for K3v2/K4v2 pool building)
  4. Build K3v2 union = top-20 by 24h ∪ top-20 by 7d ∪ top-20 by 30d-high
     Build K4v2 union = top-20 by 24h ∪ top-20 by 7d ∪ top-20 by 30d-low
  5. Run K3v2 / K4v2 on union candidates with per-stage stats
  6. Return signals + all scan records

Output per cycle (INFO log):
  [K3v2] pool: old=20 new=N (24h=20 7d=20 30d=20 union=N)
  [K3v2] stage: space=X maturity=Y exhaustion=Z structure=W entry=V signal=U
  [K4v2] pool: old=20 new=N …
  [K4v2] stage: …
"""
from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import NamedTuple

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, change_nd, close_location, consecutive_trend_days,
    ema_direction_up, ema_from_klines, has_higher_low, has_lower_high,
    range_position_30d, vol_grade, vol_ratio,
)
from binance_ai_trader.classic.models import CoinContext, ScanRecord
from binance_ai_trader.classic.strategies import c1, c2, c3, c4, k1, k2, k3, k4
from binance_ai_trader.classic.strategies import k3v2, k4v2
from binance_ai_trader.classic.universe import UniverseEntry, build_universe
from binance_ai_trader.infrastructure.binance_public import (
    BinancePublicApiError, BinancePublicClient,
)

log = logging.getLogger(__name__)

# ── Direction / name maps (existing strategies only) ─────────────────────────
_DIRECTION_MAP = {
    c1.STRATEGY_ID:       "LONG",
    c2.STRATEGY_ID:       "LONG",
    c3.STRATEGY_ID:       "SHORT",
    c4.STRATEGY_ID_TOP:   "SHORT",
    c4.STRATEGY_ID_BOT:   "LONG",
    k1.STRATEGY_ID:       "LONG",
    k2.STRATEGY_ID:       "LONG",
    k3.STRATEGY_ID:       "SHORT",
    k4.STRATEGY_ID:       "LONG",
    k3v2.STRATEGY_ID:     "SHORT",
    k4v2.STRATEGY_ID:     "LONG",
}

_STRATEGY_NAMES = {
    c1.STRATEGY_ID:     c1.STRATEGY_NAME,
    c2.STRATEGY_ID:     c2.STRATEGY_NAME,
    c3.STRATEGY_ID:     c3.STRATEGY_NAME,
    c4.STRATEGY_ID_TOP: c4.STRATEGY_NAME_TOP,
    c4.STRATEGY_ID_BOT: c4.STRATEGY_NAME_BOT,
    k1.STRATEGY_ID:     k1.STRATEGY_NAME,
    k2.STRATEGY_ID:     k2.STRATEGY_NAME,
    k3.STRATEGY_ID:     k3.STRATEGY_NAME,
    k4.STRATEGY_ID:     k4.STRATEGY_NAME,
    k3v2.STRATEGY_ID:   k3v2.STRATEGY_NAME,
    k4v2.STRATEGY_ID:   k4v2.STRATEGY_NAME,
}


# ── Kline fetching ────────────────────────────────────────────────────────────

def _fetch_klines(client: BinancePublicClient, symbol: str) -> dict | None:
    """Fetch 15m/1h/4h/1d klines. Returns None on error."""
    try:
        raw_15m = client.klines(symbol, "15m", limit=CFG.limit_15m)
        raw_1h  = client.klines(symbol, "1h",  limit=CFG.limit_1h)
        raw_4h  = client.klines(symbol, "4h",  limit=CFG.limit_4h)
        raw_1d  = client.klines(symbol, "1d",  limit=CFG.limit_1d)
        return {
            "15m": raw_15m[:-1],
            "1h":  raw_1h[:-1],
            "4h":  raw_4h[:-1],
            "1d":  raw_1d[:-1],
        }
    except (BinancePublicApiError, Exception) as exc:
        log.warning("[Classic] klines fetch failed for %s: %s", symbol, exc)
        return None


# ── Context building ──────────────────────────────────────────────────────────

def _build_context(entry: UniverseEntry, klines: dict, direction: str) -> CoinContext | None:
    k15 = klines["15m"]
    k1h = klines["1h"]
    k4h = klines["4h"]
    k1d = klines["1d"]

    if len(k15) < 22 or len(k1h) < 22 or len(k4h) < 22 or len(k1d) < 8:
        return None

    try:
        ema20_4h    = ema_from_klines(k4h, 20)
        ema60_4h    = ema_from_klines(k4h, 60)
        atr14_4h    = atr(k4h, 14)
        ema20_4h_up = ema_direction_up(k4h, 20, lookback=5)
        current_price = k15[-1].close
        dist_4h     = abs(current_price - ema20_4h)
        dist_4h_atr = dist_4h / atr14_4h if atr14_4h > 0 else Decimal("0")

        ema20_1h = ema_from_klines(k1h, 20)
        vr_1h    = vol_ratio(k1h)
        hl_1h    = has_higher_low(k1h)
        lh_1h    = has_lower_high(k1h)

        ema20_15m = ema_from_klines(k15, 20)
        atr14_15m = atr(k15, 14)
        vr_15m    = vol_ratio(k15)
        vgrade    = vol_grade(vr_15m)

        ch3d = change_nd(k1d, 3)
        ch7d = change_nd(k1d, 7)
        rp30 = range_position_30d(k1d)
        consec, consec_dir = consecutive_trend_days(k1d)

        return CoinContext(
            symbol=entry.symbol,
            direction=direction,
            pool_type="TOP_GAINERS" if entry.change_24h > 0 else "TOP_LOSERS",
            pool_rank=0,
            change_24h=entry.change_24h,
            quote_volume_24h=entry.quote_volume_24h,
            change_3d=ch3d,
            change_7d=ch7d,
            range_pos_30d=rp30,
            consec_days=consec,
            consec_direction=consec_dir,
            ema20_4h=ema20_4h,
            ema60_4h=ema60_4h,
            atr14_4h=atr14_4h,
            ema20_4h_up=ema20_4h_up,
            price_dist_4h_atr=dist_4h_atr,
            ema20_1h=ema20_1h,
            vol_ratio_1h=vr_1h,
            has_higher_low_1h=hl_1h,
            has_lower_high_1h=lh_1h,
            ema20_15m=ema20_15m,
            atr14_15m=atr14_15m,
            vol_ratio_15m=vr_15m,
            vol_grade_15m=vgrade,
            current_price=current_price,
        )
    except Exception as exc:
        log.warning("[Classic] context build failed for %s: %s", entry.symbol, exc)
        return None


# ── Strategy dispatch (existing) ──────────────────────────────────────────────

def _run_strategy(strategy_id: str, ctx: CoinContext, klines: dict) -> tuple[dict | None, list[str]]:
    k15 = klines["15m"]
    k1h = klines["1h"]
    k4h = klines["4h"]

    if strategy_id == c1.STRATEGY_ID:      return c1.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == c2.STRATEGY_ID:      return c2.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == c3.STRATEGY_ID:      return c3.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == c4.STRATEGY_ID_TOP:  return c4.evaluate_top(ctx, k15, k1h, k4h)
    if strategy_id == c4.STRATEGY_ID_BOT:  return c4.evaluate_bot(ctx, k15, k1h, k4h)
    if strategy_id == k1.STRATEGY_ID:      return k1.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == k2.STRATEGY_ID:      return k2.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == k3.STRATEGY_ID:      return k3.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == k4.STRATEGY_ID:      return k4.evaluate(ctx, k15, k1h, k4h)
    return None, [f"unknown_strategy_{strategy_id}"]


# ── K3v2/K4v2 helpers ─────────────────────────────────────────────────────────

# Carrier for a fully-processed coin (direction=SHORT for K3v2, LONG for K4v2)
class _CoinData(NamedTuple):
    entry:  UniverseEntry
    klines: dict
    ctx:    CoinContext


def _stage_bucket(rejs: list[str]) -> str:
    """Map first rejection prefix → stage bucket name."""
    if not rejs:
        return "signal"
    p = rejs[0]
    if p.startswith("SPACE_"):     return "space"
    if p.startswith("MATURITY_"):  return "maturity"
    if p.startswith("EXHAUST_"):   return "exhaustion"
    if p.startswith("PANIC_"):     return "panic"
    if p.startswith("STRUCT_"):    return "structure"
    if p.startswith("ENTRY_"):     return "entry"
    return "other"


def _build_kv2_pool(
    coin_data: list[_CoinData],
    by_7d_key: str,        # "7d_desc" or "7d_asc"
    by_30d_key: str,       # "30d_desc" or "30d_asc"
    pool_name: str,
    top_n: int = 20,
) -> frozenset[str]:
    """Build union pool for K3v2 or K4v2 from three ranked sub-lists."""
    by_24h = {d.entry.symbol for d in coin_data[:top_n]}

    if by_7d_key == "7d_desc":   # K3v2: highest 7d gain
        sorted_7d = sorted(coin_data, key=lambda d: -float(d.ctx.change_7d))
    else:                          # K4v2: lowest 7d (most negative)
        sorted_7d = sorted(coin_data, key=lambda d: float(d.ctx.change_7d))
    by_7d = {d.entry.symbol for d in sorted_7d[:top_n]}

    if by_30d_key == "30d_desc":  # K3v2: highest 30d position
        sorted_30d = sorted(coin_data, key=lambda d: -float(d.ctx.range_pos_30d))
    else:                          # K4v2: lowest 30d position
        sorted_30d = sorted(coin_data, key=lambda d: float(d.ctx.range_pos_30d))
    by_30d = {d.entry.symbol for d in sorted_30d[:top_n]}

    union = by_24h | by_7d | by_30d
    log.info(
        "[%s] pool: old=%d new=%d (24h=%d 7d=%d 30d=%d union=%d)",
        pool_name, top_n, len(union),
        len(by_24h), len(by_7d), len(by_30d), len(union),
    )
    return frozenset(union)


# ── Scan result ───────────────────────────────────────────────────────────────

class ClassicScanResult:
    def __init__(self) -> None:
        self.signals: dict[str, dict] = {}
        self.records: list[ScanRecord] = []
        self.total_coins: int = 0
        self.total_evaluated: int = 0


# ── Main scan ─────────────────────────────────────────────────────────────────

def scan(client: BinancePublicClient, now: datetime | None = None) -> ClassicScanResult:
    """Run one full Classic scan cycle. Returns signals + all scan records."""
    now = now or datetime.now(UTC)
    result = ClassicScanResult()

    # ── 1. Build EXTENDED universe (top-40 each) ───────────────────────────
    try:
        all_gainers, all_losers = build_universe(
            client, pool_size=CFG.universe_pool_size_extended
        )
    except Exception as exc:
        log.error("[Classic] universe build failed: %s", exc)
        return result

    main_gainers = all_gainers[:CFG.universe_pool_size]   # top-20
    main_losers  = all_losers[:CFG.universe_pool_size]    # top-20
    ext_gainers  = all_gainers[CFG.universe_pool_size:]   # 21-40
    ext_losers   = all_losers[CFG.universe_pool_size:]    # 21-40

    result.total_coins = len(all_gainers) + len(all_losers)

    # ── 2. Pool tasks: existing strategies on top-20 ─────────────────────
    pool_tasks: list[tuple[UniverseEntry, int, list[str]]] = []
    for rank, entry in enumerate(main_gainers, start=1):
        pool_tasks.append((entry, rank, [
            c1.STRATEGY_ID, c2.STRATEGY_ID, c4.STRATEGY_ID_TOP,
            k1.STRATEGY_ID, k2.STRATEGY_ID, k3.STRATEGY_ID,
        ]))
    for rank, entry in enumerate(main_losers, start=1):
        pool_tasks.append((entry, rank, [
            c3.STRATEGY_ID, c4.STRATEGY_ID_BOT,
            k4.STRATEGY_ID,
        ]))

    best_per_strategy: dict[str, dict] = {}
    rej_counter: dict[str, list[str]] = defaultdict(list)

    # Storage for K3v2/K4v2 pool building (built from main pool processing)
    gainer_data: list[_CoinData] = []   # direction=SHORT
    loser_data:  list[_CoinData] = []   # direction=LONG
    processed_syms: set[str] = set()

    # ── 3. Process main pool ──────────────────────────────────────────────
    for entry, rank, strategy_ids in pool_tasks:
        klines = _fetch_klines(client, entry.symbol)
        if klines is None:
            continue
        time.sleep(0.05)
        result.total_evaluated += 1
        processed_syms.add(entry.symbol)

        # Store for K3v2/K4v2
        is_gainer = entry in main_gainers
        if is_gainer:
            ctx_s = _build_context(entry, klines, "SHORT")
            if ctx_s:
                gainer_data.append(_CoinData(entry, klines, ctx_s))
        else:
            ctx_l = _build_context(entry, klines, "LONG")
            if ctx_l:
                loser_data.append(_CoinData(entry, klines, ctx_l))

        for strategy_id in strategy_ids:
            direction  = _DIRECTION_MAP[strategy_id]
            ctx_base   = _build_context(entry, klines, direction)
            if ctx_base is None:
                continue
            ctx = dataclasses.replace(ctx_base, pool_rank=rank)

            try:
                sig, rejs = _run_strategy(strategy_id, ctx, klines)
            except Exception as exc:
                log.warning("[Classic/%s] _run_strategy error %s: %s", strategy_id, entry.symbol, exc)
                sig, rejs = None, [f"strategy_exc_{type(exc).__name__}"]

            score  = sig["score"] if sig else 0
            passed = sig is not None and score >= CFG.score_signal_min
            trend_4h = (
                "BULL" if ctx.ema20_4h > ctx.ema60_4h
                else "BEAR" if ctx.ema20_4h < ctx.ema60_4h
                else "FLAT"
            )

            rec = ScanRecord(
                scan_id=str(uuid.uuid4()),
                strategy_id=strategy_id,
                scanned_at=now.isoformat(timespec="seconds"),
                symbol=entry.symbol,
                pool_type=ctx.pool_type,
                pool_rank=rank,
                direction=direction,
                change_24h=float(entry.change_24h),
                quote_volume=float(entry.quote_volume_24h),
                change_3d=float(ctx.change_3d),
                change_7d=float(ctx.change_7d),
                range_pos_30d=float(ctx.range_pos_30d),
                consec_days=ctx.consec_days,
                trend_4h=trend_4h,
                atr_dist_4h=float(ctx.price_dist_4h_atr),
                vol_ratio_1h=float(ctx.vol_ratio_1h),
                vol_ratio_15m=float(ctx.vol_ratio_15m),
                vol_grade=ctx.vol_grade_15m,
                price_pattern=sig.get("pattern_desc", "") if sig else "",
                score=score,
                passed=passed,
                entry=str(sig["entry"]) if sig else None,
                sl=str(sig["sl"]) if sig else None,
                tp1=str(sig["tp1"]) if sig else None,
                tp2=str(sig["tp2"]) if sig else None,
                rr=str(sig["rr"]) if sig else None,
                rejection="; ".join(rejs) if rejs else "",
                signal_id=None,
            )
            result.records.append(rec)

            if not passed:
                rej_counter[strategy_id].extend(rejs)
                continue

            prev = best_per_strategy.get(strategy_id)
            if prev is None or score > prev["score"]:
                best_per_strategy[strategy_id] = {
                    **sig,
                    "strategy_id": strategy_id,
                    "strategy_name": _STRATEGY_NAMES[strategy_id],
                    "symbol": entry.symbol,
                    "direction": direction,
                    "pool_type": ctx.pool_type,
                    "pool_rank": rank,
                    "change_24h": ctx.change_24h,
                    "change_3d": ctx.change_3d,
                    "change_7d": ctx.change_7d,
                    "range_pos_30d": ctx.range_pos_30d,
                    "consec_days": ctx.consec_days,
                    "dist_4h_ema_atr": ctx.price_dist_4h_atr,
                    "vol_ratio_1h": ctx.vol_ratio_1h,
                    "vol_ratio_15m": ctx.vol_ratio_15m,
                    "quote_volume_24h": ctx.quote_volume_24h,
                    "_scan_id": rec.scan_id,
                }

    # ── 4. Fetch extended pool (21-40) for K3v2/K4v2 ────────────────────
    for entry in ext_gainers:
        if entry.symbol in processed_syms:
            continue
        klines = _fetch_klines(client, entry.symbol)
        if klines is None:
            continue
        time.sleep(0.05)
        processed_syms.add(entry.symbol)
        ctx_s = _build_context(entry, klines, "SHORT")
        if ctx_s:
            gainer_data.append(_CoinData(entry, klines, ctx_s))

    for entry in ext_losers:
        if entry.symbol in processed_syms:
            continue
        klines = _fetch_klines(client, entry.symbol)
        if klines is None:
            continue
        time.sleep(0.05)
        processed_syms.add(entry.symbol)
        ctx_l = _build_context(entry, klines, "LONG")
        if ctx_l:
            loser_data.append(_CoinData(entry, klines, ctx_l))

    # ── 5. Build K3v2/K4v2 union pools ────────────────────────────────────
    k3v2_symbols = _build_kv2_pool(
        gainer_data, by_7d_key="7d_desc", by_30d_key="30d_desc",
        pool_name="K3v2",
    )
    k4v2_symbols = _build_kv2_pool(
        loser_data, by_7d_key="7d_asc", by_30d_key="30d_asc",
        pool_name="K4v2",
    )

    # ── 6. Run K3v2 ────────────────────────────────────────────────────────
    k3v2_stage: Counter = Counter()
    k3v2_eval = 0
    for idx, coin in enumerate(gainer_data):
        if coin.entry.symbol not in k3v2_symbols:
            continue
        k3v2_eval += 1
        ctx = dataclasses.replace(coin.ctx, pool_rank=idx + 1)
        try:
            sig, rejs = k3v2.evaluate(
                ctx,
                coin.klines["15m"], coin.klines["1h"],
                coin.klines["4h"], coin.klines["1d"],
            )
        except Exception as exc:
            log.warning("[Classic/k3v2] error %s: %s", coin.entry.symbol, exc)
            rejs = [f"strategy_exc_{type(exc).__name__}"]
            sig  = None

        bucket = _stage_bucket(rejs if not sig else [])
        k3v2_stage[bucket] += 1

        rej_counter[k3v2.STRATEGY_ID].extend(rejs)

        if sig is None:
            continue
        score  = sig["score"]
        passed = score >= CFG.score_signal_min
        trend_4h = (
            "BULL" if ctx.ema20_4h > ctx.ema60_4h
            else "BEAR" if ctx.ema20_4h < ctx.ema60_4h
            else "FLAT"
        )
        rec = ScanRecord(
            scan_id=str(uuid.uuid4()),
            strategy_id=k3v2.STRATEGY_ID,
            scanned_at=now.isoformat(timespec="seconds"),
            symbol=coin.entry.symbol,
            pool_type="K3V2_UNION",
            pool_rank=idx + 1,
            direction="SHORT",
            change_24h=float(coin.entry.change_24h),
            quote_volume=float(coin.entry.quote_volume_24h),
            change_3d=float(ctx.change_3d),
            change_7d=float(ctx.change_7d),
            range_pos_30d=float(ctx.range_pos_30d),
            consec_days=ctx.consec_days,
            trend_4h=trend_4h,
            atr_dist_4h=float(ctx.price_dist_4h_atr),
            vol_ratio_1h=float(ctx.vol_ratio_1h),
            vol_ratio_15m=float(ctx.vol_ratio_15m),
            vol_grade=ctx.vol_grade_15m,
            price_pattern=sig.get("pattern_desc", ""),
            score=score,
            passed=passed,
            entry=str(sig["entry"]),
            sl=str(sig["sl"]),
            tp1=str(sig["tp1"]),
            tp2=str(sig["tp2"]),
            rr=str(sig["rr"]),
            rejection="",
            signal_id=None,
        )
        result.records.append(rec)

        if passed:
            prev = best_per_strategy.get(k3v2.STRATEGY_ID)
            if prev is None or score > prev["score"]:
                best_per_strategy[k3v2.STRATEGY_ID] = {
                    **sig,
                    "strategy_id":   k3v2.STRATEGY_ID,
                    "strategy_name": k3v2.STRATEGY_NAME,
                    "symbol":        coin.entry.symbol,
                    "direction":     "SHORT",
                    "pool_type":     "K3V2_UNION",
                    "pool_rank":     idx + 1,
                    "change_24h":    ctx.change_24h,
                    "change_3d":     ctx.change_3d,
                    "change_7d":     ctx.change_7d,
                    "range_pos_30d": ctx.range_pos_30d,
                    "consec_days":   ctx.consec_days,
                    "dist_4h_ema_atr": ctx.price_dist_4h_atr,
                    "vol_ratio_1h":  ctx.vol_ratio_1h,
                    "vol_ratio_15m": ctx.vol_ratio_15m,
                    "quote_volume_24h": ctx.quote_volume_24h,
                    "_scan_id":      rec.scan_id,
                }

    log.info(
        "[K3v2] evaluated=%d stage=%s",
        k3v2_eval, dict(k3v2_stage),
    )

    # ── 7. Run K4v2 ────────────────────────────────────────────────────────
    k4v2_stage: Counter = Counter()
    k4v2_eval = 0
    for idx, coin in enumerate(loser_data):
        if coin.entry.symbol not in k4v2_symbols:
            continue
        k4v2_eval += 1
        ctx = dataclasses.replace(coin.ctx, pool_rank=idx + 1)
        try:
            sig, rejs = k4v2.evaluate(
                ctx,
                coin.klines["15m"], coin.klines["1h"],
                coin.klines["4h"], coin.klines["1d"],
            )
        except Exception as exc:
            log.warning("[Classic/k4v2] error %s: %s", coin.entry.symbol, exc)
            rejs = [f"strategy_exc_{type(exc).__name__}"]
            sig  = None

        bucket = _stage_bucket(rejs if not sig else [])
        k4v2_stage[bucket] += 1

        rej_counter[k4v2.STRATEGY_ID].extend(rejs)

        if sig is None:
            continue
        score  = sig["score"]
        passed = score >= CFG.score_signal_min
        trend_4h = (
            "BULL" if ctx.ema20_4h > ctx.ema60_4h
            else "BEAR" if ctx.ema20_4h < ctx.ema60_4h
            else "FLAT"
        )
        rec = ScanRecord(
            scan_id=str(uuid.uuid4()),
            strategy_id=k4v2.STRATEGY_ID,
            scanned_at=now.isoformat(timespec="seconds"),
            symbol=coin.entry.symbol,
            pool_type="K4V2_UNION",
            pool_rank=idx + 1,
            direction="LONG",
            change_24h=float(coin.entry.change_24h),
            quote_volume=float(coin.entry.quote_volume_24h),
            change_3d=float(ctx.change_3d),
            change_7d=float(ctx.change_7d),
            range_pos_30d=float(ctx.range_pos_30d),
            consec_days=ctx.consec_days,
            trend_4h=trend_4h,
            atr_dist_4h=float(ctx.price_dist_4h_atr),
            vol_ratio_1h=float(ctx.vol_ratio_1h),
            vol_ratio_15m=float(ctx.vol_ratio_15m),
            vol_grade=ctx.vol_grade_15m,
            price_pattern=sig.get("pattern_desc", ""),
            score=score,
            passed=passed,
            entry=str(sig["entry"]),
            sl=str(sig["sl"]),
            tp1=str(sig["tp1"]),
            tp2=str(sig["tp2"]),
            rr=str(sig["rr"]),
            rejection="",
            signal_id=None,
        )
        result.records.append(rec)

        if passed:
            prev = best_per_strategy.get(k4v2.STRATEGY_ID)
            if prev is None or score > prev["score"]:
                best_per_strategy[k4v2.STRATEGY_ID] = {
                    **sig,
                    "strategy_id":   k4v2.STRATEGY_ID,
                    "strategy_name": k4v2.STRATEGY_NAME,
                    "symbol":        coin.entry.symbol,
                    "direction":     "LONG",
                    "pool_type":     "K4V2_UNION",
                    "pool_rank":     idx + 1,
                    "change_24h":    ctx.change_24h,
                    "change_3d":     ctx.change_3d,
                    "change_7d":     ctx.change_7d,
                    "range_pos_30d": ctx.range_pos_30d,
                    "consec_days":   ctx.consec_days,
                    "dist_4h_ema_atr": ctx.price_dist_4h_atr,
                    "vol_ratio_1h":  ctx.vol_ratio_1h,
                    "vol_ratio_15m": ctx.vol_ratio_15m,
                    "quote_volume_24h": ctx.quote_volume_24h,
                    "_scan_id":      rec.scan_id,
                }

    log.info(
        "[K4v2] evaluated=%d stage=%s",
        k4v2_eval, dict(k4v2_stage),
    )

    # ── 8. Rejection summaries (existing strategies) ──────────────────────
    for sid, rejs in rej_counter.items():
        if rejs:
            top5 = Counter(rejs).most_common(5)
            log.info(
                "[Classic/%s] evaluated=%d rejected=%d | top reasons: %s",
                sid,
                sum(1 for e, _, ids in pool_tasks for s in ids if s == sid)
                if sid not in (k3v2.STRATEGY_ID, k4v2.STRATEGY_ID) else (
                    k3v2_eval if sid == k3v2.STRATEGY_ID else k4v2_eval
                ),
                len(rejs),
                top5,
            )

    # ── 9. Enforce max_total signals (best score wins) ────────────────────
    sorted_sigs = sorted(best_per_strategy.values(), key=lambda s: -s["score"])
    for sig in sorted_sigs[: CFG.max_total]:
        result.signals[sig["strategy_id"]] = sig

    log.info(
        "[Classic] scan done — coins=%d evaluated=%d signals=%d (%s)",
        result.total_coins, result.total_evaluated, len(result.signals),
        ", ".join(result.signals.keys()) or "none",
    )
    return result
