import logging
import time
from datetime import date

from market_hunter.config import MIN_AVG_DOLLAR_VOLUME, MIN_MARKET_CAP
from market_hunter.data.fmp import get_us_stock_universe
from market_hunter.data.price import (
    get_ohlcv_and_market_cap, get_spy_ohlcv,
    compute_indicators, avg_dollar_volume, get_sector_industry,
)
from market_hunter.strategies.ma60_reclaim import check_ma60_reclaim_pullback
from market_hunter.strategies.strong_trend import check_strong_trend_pullback
from market_hunter.strategies.new_high import check_new_high_breakout
from market_hunter.scoring.scorer import compute_total_score, compute_diagnostics
from market_hunter.entry_engine import compute_entry_plan, compute_trade_readiness
from market_hunter.database import db
from market_hunter.telegram import notifier

logger = logging.getLogger(__name__)


def _enrich_missing_sectors(signals: list[dict]) -> None:
    """Fill missing sector/industry via yfinance (small final set only)."""
    cache: dict[str, dict] = {}
    for sig in signals:
        if sig.get("sector"):
            continue
        sym = sig["symbol"]
        if sym not in cache:
            cache[sym] = get_sector_industry(sym)
        sig["sector"] = cache[sym]["sector"]
        sig["industry"] = cache[sym]["industry"]


def _build_diagnostics_report(
    all_signals: list[dict],
    ma60_signals: list[dict],
    strong_signals: list[dict],
    new_high_signals: list[dict],
) -> dict:
    sector_counts: dict[str, int] = {}
    for sig in all_signals:
        s = sig.get("sector") or "其他"
        sector_counts[s] = sector_counts.get(s, 0) + 1

    top_sectors = sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    avg_score = (
        sum(s["total_score"] for s in all_signals) / len(all_signals)
        if all_signals else 0.0
    )

    return {
        "ma60_count":          len(ma60_signals),
        "strong_trend_count":  len(strong_signals),
        "new_high_count":      len(new_high_signals),
        "total_signal_count":  len(ma60_signals) + len(strong_signals) + len(new_high_signals),
        "avg_score":           round(avg_score, 1),
        "top_sectors":         top_sectors,
    }


def run_us_scan(notify: bool = True) -> dict:
    """Run the full US stock daily scan."""
    start_time = time.time()
    scan_date  = date.today().isoformat()
    logger.info(f"Starting US scan — {scan_date}")

    db.init_db()

    universe = get_us_stock_universe()
    if not universe:
        error = "Failed to fetch stock universe"
        logger.error(error)
        db.insert_scan_run(scan_date, 0, 0, 0, "failed", error)
        if notify:
            notifier.send_error(error)
        return {}

    logger.info(f"Universe: {len(universe)} stocks")

    spy_df = get_spy_ohlcv()
    spy_df = compute_indicators(spy_df) if not spy_df.empty else spy_df

    all_signals:          list[dict] = []
    ma60_signals:         list[dict] = []
    strong_trend_signals: list[dict] = []
    new_high_signals:     list[dict] = []

    total_scanned           = 0
    valid_price_count       = 0
    rejected_bad_price_count = 0

    for stock in universe:
        symbol = stock.get("symbol", "")
        if not symbol:
            continue

        try:
            df, market_cap, price_valid = get_ohlcv_and_market_cap(symbol, validate=True)

            if df.empty or len(df) < 60:
                continue

            if not price_valid:
                rejected_bad_price_count += 1
                logger.warning(f"{symbol}: rejected — price cross-check failed")
                continue

            df = compute_indicators(df)

            adv = avg_dollar_volume(df)
            if adv < MIN_AVG_DOLLAR_VOLUME:
                continue

            if market_cap > 0 and market_cap < MIN_MARKET_CAP:
                continue

            valid_price_count += 1
            total_scanned     += 1

            sector = stock.get("sector", "")
            scores = compute_total_score(df, spy_df, sector=sector)
            diag   = compute_diagnostics(df, spy_df)

            last        = df.iloc[-1]
            close_price = round(float(last["Close"]), 2)
            vol_ratio   = diag.get("volume_ratio")
            total_score = scores.get("total_score", 0)

            signal_base: dict = {
                "symbol":       symbol,
                "company_name": stock.get("companyName", ""),
                "sector":       sector,
                "industry":     stock.get("industry", ""),
                "market_cap":   market_cap,
                "signal_date":  scan_date,
                "close_price":  close_price,
                "volume":       int(last["Volume"]),
                "strategies":   [],
                "diagnostics":  diag,
                **scores,
            }

            # ── Strategy A — MA60 Reclaim Pullback ───────────────────────
            ma60 = check_ma60_reclaim_pullback(df)
            if ma60["triggered"]:
                ep = compute_entry_plan("ma60_reclaim", df, ma60["details"])
                ep = compute_trade_readiness(ep, close_price, vol_ratio, total_score,
                                             "ma60_reclaim")
                signal_base["strategies"].append("ma60_reclaim")
                ma60_signals.append({
                    **signal_base,
                    "strategy_details": ma60["details"],
                    "entry_plan":       ep,
                    "_in_cooldown":     db.is_in_cooldown(symbol, "ma60_reclaim", scan_date),
                    "_in_holding":      db.is_holding(symbol),
                })

            # ── Strategy B — Strong Trend Pullback ───────────────────────
            strong = check_strong_trend_pullback(df)
            if strong["triggered"]:
                ep = compute_entry_plan("strong_trend", df, strong["details"])
                ep = compute_trade_readiness(ep, close_price, vol_ratio, total_score,
                                             "strong_trend")
                signal_base["strategies"].append("strong_trend")
                strong_trend_signals.append({
                    **signal_base,
                    "strategy_details": strong["details"],
                    "entry_plan":       ep,
                    "_in_cooldown":     db.is_in_cooldown(symbol, "strong_trend", scan_date),
                    "_in_holding":      db.is_holding(symbol),
                })

            # ── Strategy C — 52-Week High Breakout ───────────────────────
            new_high = check_new_high_breakout(df)
            if new_high["triggered"]:
                ep = compute_entry_plan("new_high", df, new_high["details"])
                ep = compute_trade_readiness(ep, close_price, vol_ratio, total_score,
                                             "new_high")
                signal_base["strategies"].append("new_high")
                new_high_signals.append({
                    **signal_base,
                    "strategy_details": new_high["details"],
                    "entry_plan":       ep,
                    "_in_cooldown":     db.is_in_cooldown(symbol, "new_high", scan_date),
                    "_in_holding":      db.is_holding(symbol),
                })

            all_signals.append(signal_base)

        except Exception as e:
            logger.warning(f"Error processing {symbol}: {e}")
            continue

    # ── Sort: actionable first, then by score ─────────────────────────────

    def _strategy_sort_key(sig: dict) -> tuple:
        ep     = sig.get("entry_plan") or {}
        status = ep.get("action_status") or ""
        return (1 if status == "可关注买点" else 0, sig["total_score"])

    all_signals.sort(key=lambda x: x["total_score"], reverse=True)
    ma60_signals.sort(key=_strategy_sort_key, reverse=True)
    strong_trend_signals.sort(key=_strategy_sort_key, reverse=True)
    new_high_signals.sort(key=_strategy_sort_key, reverse=True)

    top20       = all_signals[:20]
    top_ma60    = ma60_signals[:5]
    top_strong  = strong_trend_signals[:5]
    top_new_high = new_high_signals[:5]

    _enrich_missing_sectors(top20 + top_ma60 + top_strong + top_new_high)

    duration     = time.time() - start_time
    total_signals = len(ma60_signals) + len(strong_trend_signals) + len(new_high_signals)
    diag_report  = _build_diagnostics_report(
        all_signals, ma60_signals, strong_trend_signals, new_high_signals
    )

    logger.info(
        f"Scan complete in {duration:.1f}s — scanned {total_scanned}, "
        f"valid_price={valid_price_count}, rejected={rejected_bad_price_count}, "
        f"signals: MA60={len(ma60_signals)}, Strong={len(strong_trend_signals)}, "
        f"NewHigh={len(new_high_signals)}, avg_score={diag_report['avg_score']}"
    )

    # ── Persist ───────────────────────────────────────────────────────────

    run_id = db.insert_scan_run(
        scan_date, total_scanned, total_signals, duration,
        valid_price_count=valid_price_count,
        rejected_bad_price_count=rejected_bad_price_count,
    )

    def _save_signals(signals: list[dict], strategy_name: str) -> list[dict]:
        """
        Save all signals to DB. Return only those eligible for Telegram:
          • action_status == "可关注买点"
          • not in cooldown
          • not in holdings
          • passes re-push deduplication
        """
        telegram_eligible: list[dict] = []
        for rank, sig in enumerate(signals, 1):
            # Merge entry plan into diagnostics
            merged_diag = {**(sig.get("diagnostics") or {})}
            ep = sig.get("entry_plan") or {}
            if ep:
                merged_diag.update({
                    "entry_zone_low":  ep.get("entry_zone_low"),
                    "entry_zone_high": ep.get("entry_zone_high"),
                    "trigger_price":   ep.get("trigger_price"),
                    "stop_loss":       ep.get("stop_loss"),
                    "risk_pct":        ep.get("risk_pct"),
                    "target1":         ep.get("target1"),
                    "target2":         ep.get("target2"),
                    "rr_ratio":        ep.get("rr_ratio"),
                    "action_status":   ep.get("action_status"),
                    "action_reason":   ep.get("action_reason"),
                })
            sig["diagnostics"] = merged_diag

            sig_id  = db.upsert_signal(run_id, sig)
            details = sig.get("strategy_details", {})
            reason  = notifier._build_reason_text(
                strategy_name, details, sig.get("diagnostics") or {}
            )
            db.insert_strategy_result(
                sig_id, strategy_name, sig["symbol"], scan_date, rank,
                str(details), reason,
            )

            # Telegram eligibility — record_push is NOT called here;
            # it is called only after the Telegram send succeeds (see below).
            action_status = (sig.get("entry_plan") or {}).get("action_status", "")
            if (
                action_status == "可关注买点"
                and not sig.get("_in_cooldown", False)
                and not sig.get("_in_holding", False)
                and db.should_push_telegram(
                    sig["symbol"], strategy_name, scan_date,
                    action_status, sig.get("total_score", 0),
                )
            ):
                telegram_eligible.append(sig)

        return telegram_eligible

    tg_ma60    = _save_signals(top_ma60,    "ma60_reclaim")
    tg_strong  = _save_signals(top_strong,  "strong_trend")
    tg_new_high = _save_signals(top_new_high, "new_high")

    for sig in top20:
        db.upsert_signal(run_id, sig)

    # ── Filter stats for summary ──────────────────────────────────────────

    def _count_status(slist: list[dict], status: str) -> int:
        return sum(
            1 for s in slist
            if (s.get("entry_plan") or {}).get("action_status") == status
        )

    all_top = top_ma60 + top_strong + top_new_high
    filter_stats = {
        "total_actionable":  _count_status(all_top, "可关注买点"),
        "total_observation": sum(
            1 for s in all_top
            if (s.get("entry_plan") or {}).get("action_status", "").startswith("观察")
        ),
        "total_high_risk":   _count_status(all_top, "风险过高，等待回踩"),
        "cooldown_count":    sum(1 for s in all_top if s.get("_in_cooldown")),
        "holding_count":     sum(1 for s in all_top if s.get("_in_holding")),
        "telegram_count":    len(tg_ma60) + len(tg_strong) + len(tg_new_high),
    }

    results = {
        "scan_date":        scan_date,
        "total_scanned":    total_scanned,
        "total_signals":    total_signals,
        "top20":            top20,
        # DB-level top5 (all statuses) for reporting
        "ma60_reclaim":     top_ma60,
        "strong_trend":     top_strong,
        "new_high":         top_new_high,
        # Telegram-eligible (可关注买点 only, cooldown/holding/re-push filtered)
        "telegram_ma60":    tg_ma60,
        "telegram_strong":  tg_strong,
        "telegram_new_high": tg_new_high,
        "duration_seconds": round(duration, 1),
        "summary": {
            "scanned_count":             total_scanned,
            "valid_price_count":         valid_price_count,
            "rejected_bad_price_count":  rejected_bad_price_count,
            "signal_count":              total_signals,
        },
        "diagnostics_report": diag_report,
        "filter_stats":       filter_stats,
    }

    if notify:
        notifier.send_scan_report(scan_date, results)
        # Record pushes AFTER successful Telegram send (re-push deduplication)
        for strat_key, tg_list in [
            ("ma60_reclaim", tg_ma60),
            ("strong_trend", tg_strong),
            ("new_high",     tg_new_high),
        ]:
            for sig in tg_list:
                ep     = sig.get("entry_plan") or {}
                status = ep.get("action_status", "")
                db.record_push(
                    sig["symbol"], strat_key, scan_date,
                    status, sig.get("total_score", 0),
                )

    return results
