"""
Paper Fund — Telegram daily report (V2).

Sends a comprehensive account report after each daily run.
"""

import logging
from datetime import date as _date
from market_hunter.telegram.notifier import send_message
from market_hunter.telegram.notifier import STRATEGY_ZH, STRATEGY_EMOJI

logger = logging.getLogger(__name__)

TG_MAX = 4000


def _d(v, fmt=".2f", prefix="$") -> str:
    if v is None:
        return "N/A"
    try:
        return f"{prefix}{float(v):{fmt}}"
    except Exception:
        return str(v)


def _pct(v) -> str:
    if v is None:
        return "N/A"
    sign = "+" if float(v) >= 0 else ""
    return f"{sign}{float(v):.1f}%"


def _usd(v) -> str:
    if v is None:
        return "N/A"
    fv = float(v)
    sign = "+" if fv >= 0 else ""
    return f"{sign}${abs(fv):,.2f}" if fv < 0 else f"${fv:,.2f}" if fv >= 0 else f"-${abs(fv):,.2f}"


def _signed_usd(v) -> str:
    if v is None:
        return "N/A"
    fv = float(v)
    sign = "+" if fv >= 0 else "-"
    return f"{sign}${abs(fv):,.2f}"


def _strategy_zh(key: str | None) -> str:
    if not key:
        return "N/A"
    return f"{STRATEGY_EMOJI.get(key, '')} {STRATEGY_ZH.get(key, key)}"


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _section_account(scan_date: str, activity: dict) -> str:
    equity = activity.get("equity") or {}
    fund   = activity.get("fund") or {}

    total    = equity.get("total_equity")
    cash     = equity.get("cash")
    pos_val  = equity.get("position_value")
    pnl_d    = equity.get("pnl_daily")
    pnl_c    = equity.get("pnl_cumulative")
    pnl_cpct = equity.get("pnl_cumulative_pct")
    spy_ret  = equity.get("spy_return_pct")
    alpha    = equity.get("alpha")
    initial  = fund.get("initial_capital", 100_000)

    lines = [
        "📊 <b>Market Hunter 模拟基金</b>",
        f"📅 {scan_date}",
        "",
        "─── 账户 ───",
        f"初始资金：${initial:,.2f}",
        f"当前资产：<b>${total:,.2f}</b>" if total else "当前资产：计算中",
        f"  现金：${cash:,.2f}" if cash else "  现金：N/A",
        f"  持仓市值：${pos_val:,.2f}" if pos_val is not None else "  持仓市值：N/A",
        f"今日收益：{_signed_usd(pnl_d)}",
        f"累计收益：{_signed_usd(pnl_c)}（{_pct(pnl_cpct)}）",
        f"SPY同期：{_pct(spy_ret)}",
        f"Alpha：{_pct(alpha)}" if alpha is not None else "Alpha：N/A（数据不足）",
    ]
    return "\n".join(lines)


def _section_today_ops(activity: dict) -> str:
    filled    = activity.get("filled", [])
    cancelled = activity.get("cancelled", [])
    sold      = activity.get("sold", [])
    new_orders = activity.get("new_orders", [])

    lines = [
        "",
        "─── 今日操作 ───",
        f"新增挂单：{len(new_orders)} 只",
        f"今日成交：{len(filled)} 只",
        f"今日卖出：{len(sold)} 只",
        f"取消挂单：{len(cancelled)} 只",
    ]
    return "\n".join(lines)


def _section_positions(scan_date: str, activity: dict) -> str:
    positions = activity.get("positions", [])
    if not positions:
        return "\n─── 当前持仓（0/5）───\n  （无持仓）"

    today = _date.fromisoformat(scan_date)
    lines = [f"\n─── 当前持仓（{len(positions)}/5）───"]

    for pos in positions:
        entry_date   = pos.get("entry_date", "")
        holding_days = (today - _date.fromisoformat(entry_date)).days if entry_date else 0
        entry_price  = float(pos.get("entry_price") or 0)
        shares       = int(pos.get("shares") or 0)
        cost_basis   = float(pos.get("cost_basis") or 0)
        stop_loss    = float(pos.get("stop_loss") or 0)
        target1      = float(pos.get("target1") or 0)
        partial      = pos.get("partial_sold")
        strat        = _strategy_zh(pos.get("strategy_name"))

        # Estimate current value (use equity snapshot data if available)
        # We don't have real-time prices here, so display cost as reference
        lines += [
            f"",
            f"<b>{pos['symbol']}</b>  {strat}",
            f"  成本：${entry_price:.2f} × {shares}股 = ${cost_basis:,.2f}",
            f"  止损：${stop_loss:.2f}  目标1：${target1:.2f}",
            f"  持仓：{holding_days}天{'  ⚡已减仓50%' if partial else ''}",
        ]

    return "\n".join(lines)


def _section_pending(activity: dict) -> str:
    pending = activity.get("pending", [])
    if not pending:
        return "\n─── 明日挂单（0只）───\n  （无待成交订单）"

    lines = [f"\n─── 明日挂单（{len(pending)}只）───"]
    for o in pending:
        rr   = o.get("rr_ratio")
        risk = o.get("risk_pct")
        rr_s   = f"1:{float(rr):.1f}" if rr else "N/A"
        risk_s = f"{float(risk):.1f}%" if risk else "N/A"
        lines += [
            f"",
            f"<b>{o['symbol']}</b>  {_strategy_zh(o.get('strategy_name'))}",
            f"  触发价：${float(o.get('trigger_price',0)):.2f}  "
            f"上限：${float(o.get('cancel_limit',0)):.2f}",
            f"  止损：${float(o.get('stop_loss',0)):.2f}  "
            f"目标1：${float(o.get('target1',0)):.2f}",
            f"  RR {rr_s}  风险 {risk_s}  评分 {o.get('score') or 'N/A'}",
            f"  计划：×{o.get('planned_shares','?')}股  "
            f"≈${float(o.get('planned_cost',0)):,.0f}",
        ]
    return "\n".join(lines)


def _section_sold(activity: dict) -> str:
    sold = activity.get("sold", [])
    if not sold:
        return "\n─── 今日卖出（无）───"

    lines = [f"\n─── 今日卖出（{len(sold)}只）───"]
    for s in sold:
        pnl     = s.get("pnl", 0)
        pnl_pct = s.get("pnl_pct", 0)
        emoji   = "✅" if float(pnl or 0) >= 0 else "❌"
        lines += [
            f"",
            f"{emoji} <b>{s['symbol']}</b>  @ ${float(s.get('price',0)):.2f}",
            f"  {_signed_usd(pnl)}  {_pct(pnl_pct)}  "
            f"×{s.get('shares','?')}股",
            f"  原因：{s.get('reason','N/A')}",
        ]
    return "\n".join(lines)


def _section_filled(activity: dict) -> str:
    filled = activity.get("filled", [])
    if not filled:
        return ""
    lines = [f"\n─── 今日成交（{len(filled)}只）───"]
    for f in filled:
        lines += [
            f"",
            f"✅ <b>{f['symbol']}</b>  @ ${float(f.get('fill_price',0)):.2f}",
            f"  ×{f.get('shares','?')}股  ≈${float(f.get('cost',0)):,.0f}",
        ]
    return "\n".join(lines)


def _section_cancelled(activity: dict) -> str:
    cancelled = activity.get("cancelled", [])
    if not cancelled:
        return ""
    lines = [f"\n─── 取消挂单（{len(cancelled)}只）───"]
    for o in cancelled:
        lines.append(f"  ⚪ {o['symbol']}：{o.get('cancel_reason','N/A')}")
    return "\n".join(lines)


def _section_notices(activity: dict) -> str:
    notices = activity.get("notices", [])
    if not notices:
        return ""
    lines = ["\n─── 系统提醒 ───"]
    for n in notices:
        lines.append(f"  ⚠️ {n}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main send function
# ---------------------------------------------------------------------------

def send_daily_report(scan_date: str, activity: dict) -> bool:
    """Compose and send the full daily paper fund report to Telegram."""
    parts = [
        _section_account(scan_date, activity),
        _section_today_ops(activity),
        _section_filled(activity),
        _section_cancelled(activity),
        _section_positions(scan_date, activity),
        _section_pending(activity),
        _section_sold(activity),
        _section_notices(activity),
    ]

    full_text = "\n".join(p for p in parts if p)

    # Split into ≤TG_MAX chunks
    messages: list[str] = []
    if len(full_text) <= TG_MAX:
        messages.append(full_text)
    else:
        # Split at major section boundaries
        chunk = ""
        for part in parts:
            if not part:
                continue
            if len(chunk) + len(part) + 1 > TG_MAX:
                if chunk:
                    messages.append(chunk.strip())
                chunk = part
            else:
                chunk += "\n" + part
        if chunk.strip():
            messages.append(chunk.strip())

    ok = True
    for msg in messages:
        if msg.strip():
            ok &= send_message(msg)

    if ok:
        logger.info(f"Paper fund daily report sent ({len(messages)} message(s))")
    else:
        logger.error("Paper fund daily report failed to send")

    return ok


def send_paper_report_standalone(scan_date: str) -> bool:
    """Send report using data from DB (for `paper-report` CLI command)."""
    from market_hunter.paper_fund import db as pfdb
    pfdb.init_paper_db()
    fund     = pfdb.get_fund()
    if not fund:
        return send_message("⚠️ 模拟基金未初始化。请先运行 paper-init。")
    equity   = pfdb.get_latest_equity()
    positions = pfdb.get_open_positions()
    pending  = pfdb.get_pending_orders()
    today    = scan_date or _date.today().isoformat()
    sells    = [t for t in pfdb.get_trades_for_date(today)
                if t.get("trade_type") in ("sell_partial", "sell_full")]
    filled   = [t for t in pfdb.get_trades_for_date(today)
                if t.get("trade_type") == "buy"]

    activity = {
        "scan_date":  today,
        "filled":     filled,
        "cancelled":  [],
        "sold":       sells,
        "new_orders": [],
        "fund":       fund,
        "equity":     equity or {},
        "positions":  positions,
        "pending":    pending,
        "notices":    [],
    }
    return send_daily_report(today, activity)
