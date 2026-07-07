"""
Paper Fund — core daily logic (V2).

Flow per trading day (called after run_us_scan):
  1. process_pending_orders  — fill or cancel yesterday's orders using today's open
  2. check_sell_rules        — evaluate open positions using today's close
  3. select_buy_candidates   — filter today's scan signals by buy rules
  4. create_orders           — queue eligible candidates for tomorrow's open
  5. snapshot_equity         — record daily account state + SPY comparison
  6. send_daily_report       — Telegram output

Constants (all enforced, never bypassed):
  Initial capital  $100,000
  Max positions    5
  Max new/day      2
  Max position     20 % of equity
  Min cash         10 % of equity
  Max holding      30 days
"""

import logging
import pandas as pd
from datetime import date as _date, timedelta

from market_hunter.paper_fund import db as pfdb

logger = logging.getLogger(__name__)

# ── Fund constants ───────────────────────────────────────────────────────────
MAX_POSITIONS    = 5
MAX_NEW_PER_DAY  = 2
MAX_POS_PCT      = 0.20
MIN_CASH_PCT     = 0.10
MAX_HOLDING_DAYS = 30
CANCEL_BUFFER    = 1.02    # cancel order if open > trigger × 1.02

# ── Buy-filter thresholds ────────────────────────────────────────────────────
BUY_MIN_SCORE  = 75.0       # relaxed from 85 — 85 was too strict in current market
BUY_MIN_RR     = 2.0
BUY_MAX_RISK   = 7.0        # relaxed from 6 — allow slightly wider stops
BUY_MIN_VOL    = 1.2
BUY_MIN_STARS  = 3          # relaxed from 4 — ★★★ minimum (可关注买点 earns 3★)


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------

def _yf_sym(symbol: str) -> str:
    return symbol.replace(".", "-")


def _get_day_prices(symbol: str, date_str: str) -> dict:
    """Return {open, high, low, close, volume} for symbol on date_str (or closest prior trading day)."""
    try:
        import yfinance as yf
        sym = _yf_sym(symbol)
        d   = _date.fromisoformat(date_str)
        df  = yf.Ticker(sym).history(
            start=(d - timedelta(days=7)).isoformat(),
            end=(d + timedelta(days=2)).isoformat(),
            auto_adjust=True,
        )
        if df.empty:
            return {}
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index = df.index.normalize()
        target = pd.Timestamp(date_str)
        if target in df.index:
            row = df.loc[target]
        else:
            past = df[df.index <= target]
            if past.empty:
                return {}
            row = past.iloc[-1]
        return {
            "open":   round(float(row["Open"]),   2),
            "high":   round(float(row["High"]),   2),
            "low":    round(float(row["Low"]),     2),
            "close":  round(float(row["Close"]),  2),
            "volume": int(row["Volume"]),
        }
    except Exception as e:
        logger.debug(f"Price fetch {symbol}@{date_str}: {e}")
        return {}


def _get_ma20(symbol: str, date_str: str) -> float | None:
    """Return the 20-day close MA for symbol as of date_str."""
    try:
        import yfinance as yf
        sym = _yf_sym(symbol)
        d   = _date.fromisoformat(date_str)
        df  = yf.Ticker(sym).history(
            start=(d - timedelta(days=65)).isoformat(),
            end=(d + timedelta(days=2)).isoformat(),
            auto_adjust=True,
        )
        if df.empty or len(df) < 20:
            return None
        return round(float(df["Close"].rolling(20).mean().iloc[-1]), 2)
    except Exception:
        return None


def _get_spy_close(date_str: str) -> float | None:
    prices = _get_day_prices("SPY", date_str)
    return prices.get("close")


# ---------------------------------------------------------------------------
# Star count (mirrors notifier._star_rating logic as integer)
# ---------------------------------------------------------------------------

def _star_count(ep: dict, score: float, vol_ratio: float | None) -> int:
    rr   = float(ep.get("rr_ratio")  or 0)
    risk = float(ep.get("risk_pct")  or 999)
    vr   = float(vol_ratio or 0)
    if score >= 90 and rr >= 2.5 and risk <= 5 and vr >= 1.5:
        return 5
    if score >= 85 and rr >= 2.0 and risk <= 6:
        return 4
    if ep.get("action_status") == "可关注买点":
        return 3
    if "风险" in (ep.get("action_status") or ""):
        return 2
    return 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_fund() -> dict:
    """
    Initialize paper fund with $100,000.
    Safe to call multiple times — idempotent (INSERT OR IGNORE).
    """
    pfdb.init_paper_db()
    fund = pfdb.init_fund()
    logger.info(f"Paper fund initialized — cash ${fund['current_cash']:,.2f}")
    return fund


def run_daily(
    scan_date: str,
    scan_results: dict | None = None,
    notify: bool = True,
) -> dict:
    """
    Main daily routine.  Call this once per trading day after run_us_scan().

    scan_results  — output of run_us_scan(); if None, buy step is skipped.
    notify        — send Telegram daily report.
    """
    pfdb.init_paper_db()
    fund = pfdb.get_fund()
    if not fund:
        logger.warning("Paper fund not initialized — run `paper-init` first")
        return {"error": "not_initialized"}

    logger.info(f"Paper Fund daily run — {scan_date}")

    # ── Step 1: Process pending orders (fill/cancel at today's open) ──────
    order_activity = _process_pending_orders(scan_date)

    # ── Step 2: Sell rules (evaluate using today's close) ─────────────────
    sells = _check_sell_rules(scan_date)

    # ── Step 3 + 4: Buy candidates → create pending orders ───────────────
    new_orders: list[dict] = []
    notices:    list[str]  = []

    fund          = pfdb.get_fund()
    open_positions = pfdb.get_open_positions()
    open_count    = len(open_positions)

    if scan_results:
        candidates = _select_buy_candidates(scan_results)
        if open_count >= MAX_POSITIONS:
            notices.append(f"仓位已满（{open_count}/{MAX_POSITIONS}），今日不新增")
        else:
            new_orders, order_notices = _create_orders(candidates, scan_date, fund, open_count)
            notices.extend(order_notices)
            if not candidates:
                notices.append("今日无符合条件的可买股票（★★★★+ 评级要求）")
    else:
        notices.append("本次运行未提供扫描结果，跳过买入步骤")

    # ── Step 5: Snapshot equity ───────────────────────────────────────────
    fund           = pfdb.get_fund()
    open_positions = pfdb.get_open_positions()
    equity         = _snapshot_equity(scan_date, fund, open_positions)

    activity = {
        "scan_date":  scan_date,
        "filled":     order_activity["filled"],
        "cancelled":  order_activity["cancelled"],
        "sold":       sells,
        "new_orders": new_orders,
        "fund":       pfdb.get_fund(),
        "equity":     equity,
        "positions":  pfdb.get_open_positions(),
        "pending":    pfdb.get_pending_orders(),
        "notices":    notices,
    }

    # ── Step 6: Telegram ──────────────────────────────────────────────────
    if notify:
        try:
            from market_hunter.paper_fund.reporter import send_daily_report
            send_daily_report(scan_date, activity)
        except Exception as e:
            logger.error(f"Paper fund Telegram report failed: {e}", exc_info=True)

    return activity


# ---------------------------------------------------------------------------
# Step 1 — process pending orders
# ---------------------------------------------------------------------------

def _process_pending_orders(scan_date: str) -> dict:
    fund    = pfdb.get_fund()
    pending = pfdb.get_pending_orders()
    filled: list[dict]    = []
    cancelled: list[dict] = []
    today = _date.fromisoformat(scan_date)

    for order in pending:
        # Cancel orders older than 3 calendar days
        age = (today - _date.fromisoformat(order["order_date"])).days
        if age > 3:
            pfdb.update_order(order["id"], status="cancelled", cancel_reason="超时取消")
            cancelled.append({**order, "cancel_reason": "超时取消"})
            continue

        prices     = _get_day_prices(order["symbol"], scan_date)
        open_price = prices.get("open")
        if open_price is None:
            logger.debug(f"No open price for {order['symbol']} on {scan_date}, skipping")
            continue

        # Cancel if open price jumped above cancel limit
        if open_price > order["cancel_limit"]:
            reason = (
                f"开盘跳空过高"
                f"（开盘 {open_price:.2f} > 上限 {order['cancel_limit']:.2f}）"
            )
            pfdb.update_order(order["id"], status="cancelled",
                              cancel_reason=reason, fill_price=open_price)
            cancelled.append({**order, "cancel_reason": reason, "open_price": open_price})
            continue

        # Capacity / cash checks
        open_positions = pfdb.get_open_positions()
        if len(open_positions) >= MAX_POSITIONS:
            pfdb.update_order(order["id"], status="cancelled", cancel_reason="仓位已满")
            cancelled.append({**order, "cancel_reason": "仓位已满"})
            continue

        if pfdb.get_position_by_symbol(order["symbol"]):
            pfdb.update_order(order["id"], status="cancelled", cancel_reason="已持有该股票")
            cancelled.append({**order, "cancel_reason": "已持有该股票"})
            continue

        shares = order["planned_shares"] or 1
        cost   = round(shares * open_price, 2)
        fund   = pfdb.get_fund()

        if fund["current_cash"] < cost:
            pfdb.update_order(order["id"], status="cancelled", cancel_reason="现金不足")
            cancelled.append({**order, "cancel_reason": "现金不足"})
            continue

        # ── Fill ────────────────────────────────────────────────────────
        pfdb.update_order(order["id"], status="filled",
                          fill_date=scan_date, fill_price=open_price)

        pos_id = pfdb.insert_position({
            "symbol":        order["symbol"],
            "strategy_name": order.get("strategy_name"),
            "entry_date":    scan_date,
            "entry_price":   open_price,
            "shares":        shares,
            "cost_basis":    cost,
            "stop_loss":     order["stop_loss"],
            "target1":       order["target1"],
            "target2":       order.get("target2"),
            "rr_ratio":      order.get("rr_ratio"),
            "risk_pct":      order.get("risk_pct"),
            "score":         order.get("score"),
        })
        pfdb.insert_trade({
            "symbol":      order["symbol"],
            "trade_type":  "buy",
            "trade_date":  scan_date,
            "price":       open_price,
            "shares":      shares,
            "amount":      cost,
            "reason":      "信号买入",
            "position_id": pos_id,
            "order_id":    order["id"],
        })
        pfdb.update_fund_cash(fund["current_cash"] - cost)

        filled.append({
            **order,
            "fill_price": open_price,
            "shares":     shares,
            "cost":       cost,
        })
        logger.info(f"Order FILLED: {order['symbol']} × {shares} @ ${open_price:.2f}")

    return {"filled": filled, "cancelled": cancelled}


# ---------------------------------------------------------------------------
# Step 2 — sell rules
# ---------------------------------------------------------------------------

def _check_sell_rules(scan_date: str) -> list[dict]:
    positions = pfdb.get_open_positions()
    sold: list[dict] = []
    today = _date.fromisoformat(scan_date)

    for pos in positions:
        holding_days = (today - _date.fromisoformat(pos["entry_date"])).days
        prices = _get_day_prices(pos["symbol"], scan_date)
        close  = prices.get("close")
        if close is None:
            logger.debug(f"No close price for {pos['symbol']} on {scan_date}")
            continue

        stop_loss = float(pos["stop_loss"])
        target1   = float(pos["target1"])
        shares    = int(pos["shares"])
        if shares <= 0:
            continue

        sell_type   = None   # "full" | "partial"
        sell_reason = None
        sell_shares = 0

        # ── Priority 1: Stop loss ────────────────────────────────────────
        if close <= stop_loss:
            sell_type   = "full"
            sell_reason = "止损"
            sell_shares = shares

        # ── Priority 2: Max holding days ────────────────────────────────
        elif holding_days >= MAX_HOLDING_DAYS:
            sell_type   = "full"
            sell_reason = f"持仓{holding_days}天，时间止损"
            sell_shares = shares

        # ── Priority 3 / 4: Target1 rules ───────────────────────────────
        elif close >= target1:
            if not pos["partial_sold"]:
                # First time hitting target1 → sell 50%
                sell_shares = max(1, shares // 2)
                sell_type   = "partial"
                sell_reason = "达到目标1，减仓50%"
            else:
                # Already partially sold → sell remaining when close < MA20
                ma20 = _get_ma20(pos["symbol"], scan_date)
                if ma20 and close < ma20:
                    sell_type   = "full"
                    sell_reason = "目标1已达，收盘跌破MA20，清仓剩余"
                    sell_shares = shares

        if sell_type is None:
            continue

        # ── Execute sell ─────────────────────────────────────────────────
        cost_per_share = pos["cost_basis"] / pos["shares"]
        cost_of_sold   = round(cost_per_share * sell_shares, 2)
        proceeds       = round(sell_shares * close, 2)
        pnl            = round(proceeds - cost_of_sold, 2)
        pnl_pct        = round((proceeds / cost_of_sold - 1) * 100, 2) if cost_of_sold else 0

        if sell_type == "partial":
            pfdb.update_position(
                pos["id"],
                shares           = shares - sell_shares,
                cost_basis       = round(pos["cost_basis"] - cost_of_sold, 2),
                partial_sold     = 1,
                partial_sold_price = close,
                partial_sold_date  = scan_date,
                partial_sold_pnl   = pnl,
            )
        else:
            pfdb.close_position(pos["id"], scan_date, sell_reason, close)

        pfdb.insert_trade({
            "symbol":      pos["symbol"],
            "trade_type":  "sell_partial" if sell_type == "partial" else "sell_full",
            "trade_date":  scan_date,
            "price":       close,
            "shares":      sell_shares,
            "amount":      proceeds,
            "cost_basis":  cost_of_sold,
            "pnl":         pnl,
            "pnl_pct":     pnl_pct,
            "reason":      sell_reason,
            "position_id": pos["id"],
        })

        fund = pfdb.get_fund()
        pfdb.update_fund_cash(fund["current_cash"] + proceeds)
        logger.info(
            f"Sell {sell_type.upper()}: {pos['symbol']} ×{sell_shares} @ ${close:.2f}  "
            f"PnL ${pnl:+.2f} ({pnl_pct:+.1f}%)  Reason: {sell_reason}"
        )

        sold.append({
            "symbol":      pos["symbol"],
            "strategy":    pos.get("strategy_name"),
            "price":       close,
            "shares":      sell_shares,
            "proceeds":    proceeds,
            "pnl":         pnl,
            "pnl_pct":     pnl_pct,
            "reason":      sell_reason,
            "sell_type":   sell_type,
        })

    return sold


# ---------------------------------------------------------------------------
# Step 3 — select buy candidates
# ---------------------------------------------------------------------------

def _select_buy_candidates(scan_results: dict) -> list[dict]:
    """
    Filter Telegram-eligible signals from today's scan for Paper Fund buy rules.

    Buy rules (ALL must be true):
      • action_status == "可关注买点"
      • ★★★★ or higher (stars >= 4)
      • score  >= 85
      • RR     >= 2.0
      • risk   <= 6%
      • vol    >= 1.2
    """
    candidates: list[dict] = []

    strategy_keys = {
        "telegram_ma60":    "ma60_reclaim",
        "telegram_strong":  "strong_trend",
        "telegram_new_high": "new_high",
    }

    for result_key, strat_name in strategy_keys.items():
        for sig in scan_results.get(result_key, []):
            ep        = sig.get("entry_plan") or {}
            diag      = sig.get("diagnostics") or {}
            score     = float(sig.get("total_score") or 0)
            rr        = float(ep.get("rr_ratio")  or 0)
            risk      = float(ep.get("risk_pct")  or 999)
            vol_ratio = diag.get("volume_ratio")
            vr        = float(vol_ratio or 0)
            stars     = _star_count(ep, score, vol_ratio)

            if ep.get("action_status") != "可关注买点":
                continue
            if stars < BUY_MIN_STARS:
                continue
            if score < BUY_MIN_SCORE:
                continue
            if rr < BUY_MIN_RR:
                continue
            if risk > BUY_MAX_RISK:
                continue
            if vr < BUY_MIN_VOL:
                continue

            candidates.append({
                **sig,
                "_stars":         stars,
                "_strategy_name": strat_name,
            })

    # Sort: stars desc → score desc → rr desc → vol desc
    candidates.sort(
        key=lambda x: (
            x["_stars"],
            float(x.get("total_score") or 0),
            float((x.get("entry_plan") or {}).get("rr_ratio") or 0),
            float((x.get("diagnostics") or {}).get("volume_ratio") or 0),
        ),
        reverse=True,
    )
    return candidates


# ---------------------------------------------------------------------------
# Step 4 — create pending orders
# ---------------------------------------------------------------------------

def _create_orders(
    candidates: list[dict],
    scan_date:  str,
    fund:       dict,
    open_count: int,
) -> tuple[list[dict], list[str]]:
    """
    Create up to MAX_NEW_PER_DAY pending orders from candidates.
    Returns (created_orders, notices).
    """
    created: list[dict] = []
    notices: list[str]  = []

    # Use latest equity snapshot for sizing (fall back to current cash)
    latest_eq = pfdb.get_latest_equity()
    total_equity = (
        latest_eq["total_equity"] if latest_eq else fund["current_cash"]
    )
    cash_reserve  = total_equity * MIN_CASH_PCT
    available_pos = MAX_POSITIONS - open_count

    reserved_cash = 0.0  # track orders created this loop

    for sig in candidates:
        if len(created) >= MAX_NEW_PER_DAY:
            break
        if available_pos <= 0:
            break

        symbol = sig["symbol"]
        ep     = sig.get("entry_plan") or {}

        if pfdb.get_position_by_symbol(symbol):
            continue
        if pfdb.has_pending_order(symbol):
            continue

        trigger = float(ep.get("trigger_price") or ep.get("entry_zone_low") or 0)
        if trigger <= 0:
            continue

        cancel_limit  = round(trigger * CANCEL_BUFFER, 2)
        max_pos_cash  = total_equity * MAX_POS_PCT
        free_cash     = fund["current_cash"] - cash_reserve - cash_reserve  # double-count safety
        usable_cash   = fund["current_cash"] - reserved_cash - cash_reserve
        position_cash = min(max_pos_cash, usable_cash)

        if position_cash < trigger:
            notices.append(f"{symbol}：可用资金不足，跳过")
            continue

        shares       = max(1, int(position_cash / trigger))
        planned_cost = round(shares * trigger, 2)

        oid = pfdb.insert_order({
            "symbol":        symbol,
            "strategy_name": sig.get("_strategy_name"),
            "order_date":    scan_date,
            "trigger_price": trigger,
            "cancel_limit":  cancel_limit,
            "stop_loss":     float(ep.get("stop_loss")  or 0),
            "target1":       float(ep.get("target1")    or 0),
            "target2":       ep.get("target2"),
            "rr_ratio":      ep.get("rr_ratio"),
            "risk_pct":      ep.get("risk_pct"),
            "score":         sig.get("total_score"),
            "planned_shares": shares,
            "planned_cost":   planned_cost,
        })

        reserved_cash += planned_cost
        available_pos -= 1
        created.append({**sig, "_order_id": oid, "_planned_shares": shares,
                         "_planned_cost": planned_cost})
        logger.info(
            f"Order QUEUED: {symbol} ×{shares} @ trigger ${trigger:.2f} "
            f"(cancel if open > ${cancel_limit:.2f})"
        )

    return created, notices


# ---------------------------------------------------------------------------
# Step 5 — equity snapshot
# ---------------------------------------------------------------------------

def _snapshot_equity(scan_date: str, fund: dict, open_positions: list[dict]) -> dict:
    """Compute and store today's equity including SPY comparison."""
    position_value = 0.0
    for pos in open_positions:
        prices = _get_day_prices(pos["symbol"], scan_date)
        close  = prices.get("close")
        if close:
            position_value += pos["shares"] * close

    total_equity   = round(fund["current_cash"] + position_value, 2)
    initial_cap    = fund["initial_capital"]
    pnl_cumulative = round(total_equity - initial_cap, 2)
    pnl_cum_pct    = round((total_equity / initial_cap - 1) * 100, 2)

    prev = pfdb.get_latest_equity()
    pnl_daily = round(total_equity - prev["total_equity"], 2) if prev else 0.0

    spy_close      = _get_spy_close(scan_date)
    spy_return_pct = None
    alpha          = None

    if spy_close:
        earliest = pfdb.get_earliest_equity()
        if earliest and earliest.get("spy_close"):
            spy_start      = float(earliest["spy_close"])
            spy_return_pct = round((spy_close / spy_start - 1) * 100, 2)
            alpha          = round(pnl_cum_pct - spy_return_pct, 2)

    eq = {
        "equity_date":        scan_date,
        "cash":               round(fund["current_cash"], 2),
        "position_value":     round(position_value, 2),
        "total_equity":       total_equity,
        "spy_close":          spy_close,
        "pnl_daily":          pnl_daily,
        "pnl_cumulative":     pnl_cumulative,
        "pnl_cumulative_pct": pnl_cum_pct,
        "spy_return_pct":     spy_return_pct,
        "alpha":              alpha,
    }
    pfdb.upsert_equity(eq)
    logger.info(
        f"Equity snapshot: ${total_equity:,.2f}  "
        f"(cash ${fund['current_cash']:,.2f}  pos ${position_value:,.2f})  "
        f"PnL {pnl_cum_pct:+.1f}%  Alpha {alpha:+.1f}%" if alpha else
        f"Equity snapshot: ${total_equity:,.2f}"
    )
    return eq
