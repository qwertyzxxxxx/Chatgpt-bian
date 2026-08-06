"""Classic C1-C4 master scanner.

Single cycle:
  1. Build universe (30M filter, top-20 gainers + top-20 losers)
  2. Fetch multi-timeframe klines for each coin
  3. Compute shared indicators (CoinContext)
  4. Run C1/C2 on gainers, C3 on losers, C4-top on gainers, C4-bot on losers
  5. Return signals per strategy_id (max 1 per strategy, max 3 total)
"""
from __future__ import annotations

import logging
import time
import uuid
import dataclasses
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.classic.config import CFG
from binance_ai_trader.classic.indicators import (
    atr, change_nd, close_location, consecutive_trend_days,
    ema_direction_up, ema_from_klines, has_higher_low, has_lower_high,
    range_position_30d, vol_grade, vol_ratio,
)
from binance_ai_trader.classic.models import CoinContext, ScanRecord
from binance_ai_trader.classic.strategies import c1, c2, c3, c4, k1, k2, k3, k4
from binance_ai_trader.classic.universe import UniverseEntry, build_universe
from binance_ai_trader.infrastructure.binance_public import (
    BinancePublicApiError, BinancePublicClient,
)

log = logging.getLogger(__name__)

# map strategy_id → (direction assigned to coins in the pool)
_DIRECTION_MAP = {
    c1.STRATEGY_ID:       "LONG",
    c2.STRATEGY_ID:       "LONG",
    c3.STRATEGY_ID:       "SHORT",
    c4.STRATEGY_ID_TOP:   "SHORT",
    c4.STRATEGY_ID_BOT:   "LONG",
    # K1-K4 新策略
    k1.STRATEGY_ID:       "LONG",
    k2.STRATEGY_ID:       "LONG",
    k3.STRATEGY_ID:       "SHORT",
    k4.STRATEGY_ID:       "LONG",
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
}


def _fetch_klines(client: BinancePublicClient, symbol: str) -> dict | None:
    """Fetch 15m/1h/4h/1d klines for a symbol. Returns None on error."""
    try:
        raw_15m = client.klines(symbol, "15m", limit=CFG.limit_15m)
        raw_1h  = client.klines(symbol, "1h",  limit=CFG.limit_1h)
        raw_4h  = client.klines(symbol, "4h",  limit=CFG.limit_4h)
        raw_1d  = client.klines(symbol, "1d",  limit=CFG.limit_1d)
        # Exclude the currently-forming candle (last element)
        return {
            "15m": raw_15m[:-1],
            "1h":  raw_1h[:-1],
            "4h":  raw_4h[:-1],
            "1d":  raw_1d[:-1],
        }
    except (BinancePublicApiError, Exception) as exc:
        log.warning("[Classic] klines fetch failed for %s: %s", symbol, exc)
        return None


def _build_context(entry: UniverseEntry, klines: dict, direction: str) -> CoinContext | None:
    """Compute all shared indicators for a coin."""
    k15 = klines["15m"]
    k1h = klines["1h"]
    k4h = klines["4h"]
    k1d = klines["1d"]

    if len(k15) < 22 or len(k1h) < 22 or len(k4h) < 22 or len(k1d) < 8:
        return None

    try:
        ema20_4h = ema_from_klines(k4h, 20)
        ema60_4h = ema_from_klines(k4h, 60)
        atr14_4h = atr(k4h, 14)
        ema20_4h_up = ema_direction_up(k4h, 20, lookback=5)
        current_price = k15[-1].close
        dist_4h = abs(current_price - ema20_4h)
        dist_4h_atr = dist_4h / atr14_4h if atr14_4h > 0 else Decimal("0")

        ema20_1h = ema_from_klines(k1h, 20)
        vr_1h = vol_ratio(k1h)
        hl_1h = has_higher_low(k1h)
        lh_1h = has_lower_high(k1h)

        ema20_15m = ema_from_klines(k15, 20)
        atr14_15m = atr(k15, 14)
        vr_15m = vol_ratio(k15)
        vgrade = vol_grade(vr_15m)

        ch3d = change_nd(k1d, 3)
        ch7d = change_nd(k1d, 7)
        rp30 = range_position_30d(k1d)
        consec, consec_dir = consecutive_trend_days(k1d)

        return CoinContext(
            symbol=entry.symbol,
            direction=direction,
            pool_type="TOP_GAINERS" if entry.change_24h > 0 else "TOP_LOSERS",
            pool_rank=0,            # set by caller
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


def _run_strategy(
    strategy_id: str,
    ctx: CoinContext,
    klines: dict,
) -> tuple[dict | None, list[str]]:
    """Dispatch to the correct strategy evaluator."""
    k15 = klines["15m"]
    k1h = klines["1h"]
    k4h = klines["4h"]

    if strategy_id == c1.STRATEGY_ID:
        return c1.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == c2.STRATEGY_ID:
        return c2.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == c3.STRATEGY_ID:
        return c3.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == c4.STRATEGY_ID_TOP:
        return c4.evaluate_top(ctx, k15, k1h, k4h)
    if strategy_id == c4.STRATEGY_ID_BOT:
        return c4.evaluate_bot(ctx, k15, k1h, k4h)
    if strategy_id == k1.STRATEGY_ID:
        return k1.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == k2.STRATEGY_ID:
        return k2.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == k3.STRATEGY_ID:
        return k3.evaluate(ctx, k15, k1h, k4h)
    if strategy_id == k4.STRATEGY_ID:
        return k4.evaluate(ctx, k15, k1h, k4h)
    return None, [f"unknown_strategy_{strategy_id}"]


class ClassicScanResult:
    def __init__(self) -> None:
        # strategy_id → best signal dict (score ≥ 75)
        self.signals: dict[str, dict] = {}
        # all scan records for DB storage
        self.records: list[ScanRecord] = []
        self.total_coins: int = 0
        self.total_evaluated: int = 0


def scan(client: BinancePublicClient, now: datetime | None = None) -> ClassicScanResult:
    """Run one full Classic scan cycle. Returns signals + all scan records."""
    now = now or datetime.now(UTC)
    result = ClassicScanResult()

    # 1. Build universe
    try:
        gainers, losers = build_universe(client)
    except Exception as exc:
        log.error("[Classic] universe build failed: %s", exc)
        return result

    result.total_coins = len(gainers) + len(losers)

    # 2. Pool assignments: which strategies scan which pool
    pool_tasks: list[tuple[UniverseEntry, int, list[str]]] = []
    for rank, entry in enumerate(gainers, start=1):
        pool_tasks.append((entry, rank, [
            c1.STRATEGY_ID, c2.STRATEGY_ID, c4.STRATEGY_ID_TOP,
            k1.STRATEGY_ID, k2.STRATEGY_ID, k3.STRATEGY_ID,
        ]))
    for rank, entry in enumerate(losers, start=1):
        pool_tasks.append((entry, rank, [
            c3.STRATEGY_ID, c4.STRATEGY_ID_BOT,
            k4.STRATEGY_ID,
        ]))

    # Collect best signal per strategy (score ≥ 75)
    best_per_strategy: dict[str, dict] = {}
    # Collect rejection reasons per strategy for INFO-level summary
    rej_counter: dict[str, list[str]] = defaultdict(list)

    for entry, rank, strategy_ids in pool_tasks:
        klines = _fetch_klines(client, entry.symbol)
        if klines is None:
            continue
        time.sleep(0.05)   # light rate-limit buffer

        result.total_evaluated += 1

        for strategy_id in strategy_ids:
            direction = _DIRECTION_MAP[strategy_id]
            ctx_base = _build_context(entry, klines, direction)
            if ctx_base is None:
                continue

            # Patch pool_rank onto ctx (dataclass frozen → rebuild)
            ctx = dataclasses.replace(ctx_base, pool_rank=rank)

            try:
                sig, rejs = _run_strategy(strategy_id, ctx, klines)
            except Exception as exc:
                log.warning(
                    "[Classic/%s] _run_strategy error on %s: %s",
                    strategy_id, entry.symbol, exc,
                )
                sig, rejs = None, [f"strategy_exc_{type(exc).__name__}"]

            # Build scan record regardless of signal
            score   = sig["score"] if sig else 0
            passed  = sig is not None and score >= CFG.score_signal_min
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
                signal_id=None,  # filled later if order created
            )
            result.records.append(rec)

            if not passed:
                rej_counter[strategy_id].extend(rejs)
                log.debug(
                    "[Classic/%s] %s SKIP score=%d rejs=%s",
                    strategy_id, entry.symbol, score, rejs,
                )
                continue

            # Keep best score per strategy
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

    # 3. Enforce max 3 total signals, sorted by score desc
    sorted_sigs = sorted(best_per_strategy.values(), key=lambda s: -s["score"])
    for sig in sorted_sigs[: CFG.max_total]:
        result.signals[sig["strategy_id"]] = sig

    # INFO-level rejection summary — critical for diagnosing zero-signal situations
    for sid, rejs in rej_counter.items():
        if rejs:
            top5 = Counter(rejs).most_common(5)
            log.info(
                "[Classic/%s] evaluated=%d rejected=%d | top reasons: %s",
                sid,
                sum(1 for e, _, ids in pool_tasks for s in ids if s == sid),
                len(rejs),
                top5,
            )

    log.info(
        "[Classic] scan done — coins=%d evaluated=%d signals=%d (%s)",
        result.total_coins, result.total_evaluated, len(result.signals),
        ", ".join(result.signals.keys()) or "none",
    )
    return result
