"""V2 Hourly Shadow Report — [V2] Hotlist Paper

Sections:
  🏆 总绩效         win_rate / avg_rr / avg_pnl / trade_count / fill_rate / expire_rate
  📂 当前持仓        FILLED orders (entry hit, waiting for TP/SL)
  📋 当前挂单        OPEN orders (waiting for entry fill)
  ✅ 最近结算        last 7 closed orders

Deliberately NO runner / API / debug info — health check is separate.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v2.paper_portfolio.repository import V2PaperOrder, V2PaperOrderRepository
from binance_ai_trader.v2.performance.calculator import V2Performance, V2PerformanceCalculator

log = logging.getLogger(__name__)


def _hold_duration(start_iso: str | None) -> str:
    if not start_iso:
        return "—"
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        delta = datetime.now(UTC) - start
        total = int(delta.total_seconds())
        h, rem = divmod(max(0, total), 3600)
        m = rem // 60
        return f"{h}h{m:02d}m" if h else f"{m}m"
    except Exception:
        return "—"


def _pnl_str(pnl) -> str:
    if pnl is None:
        return "—"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.2f}%"


def _dur_str(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


class V2ShadowReporter:
    def __init__(
        self,
        notifier: TelegramNotifier,
        order_repo: V2PaperOrderRepository,
        perf_calc: V2PerformanceCalculator,
        strategy_id: str,
    ) -> None:
        self._notifier = notifier
        self._order_repo = order_repo
        self._perf_calc = perf_calc
        self._strategy_id = strategy_id

    def send_report(self) -> None:
        try:
            msg = self._build_message()
            self._notifier.send(msg)
            log.info("[V2] hourly shadow report sent")
        except Exception as exc:
            log.warning("[V2] failed to send shadow report: %s", exc)

    def _build_message(self) -> str:
        perf = self._perf_calc.calculate(self._strategy_id)
        open_orders = self._order_repo.load_open()
        recent_settled = self._order_repo.load_recent_settled(7)

        filled_orders = [o for o in open_orders if o.status == "FILLED"]
        pending_orders = [o for o in open_orders if o.status == "OPEN"]

        lines: list[str] = ["[V2] Hotlist Paper\n"]

        lines.append(_perf_section(perf))
        lines.append(_positions_section(filled_orders))
        lines.append(_pending_section(pending_orders))
        lines.append(_recent_settled_section(recent_settled))

        return "\n".join(lines)


def _perf_section(perf: V2Performance) -> str:
    total = perf.orders
    fill_rate = (
        f"{(perf.filled + perf.open_count) / total * 100:.0f}%"
        if total > 0 else "—"
    )
    expire_rate = (
        f"{perf.expired_not_filled / total * 100:.0f}%"
        if total > 0 else "—"
    )
    return (
        "🏆 总绩效\n"
        f"胜率:     {perf.win_rate}%\n"
        f"RR:       {perf.avg_rr}\n"
        f"平均收益: {perf.avg_pnl}%\n"
        f"交易数:   {total}\n"
        f"成交率:   {fill_rate}\n"
        f"过期率:   {expire_rate}"
    )


def _positions_section(orders: list[V2PaperOrder]) -> str:
    if not orders:
        return "\n📂 当前持仓\n共 0 笔"
    rows = [f"\n📂 当前持仓\n共 {len(orders)} 笔"]
    for o in orders:
        hold = _hold_duration(o.filled_at)
        rows.append(f"  {o.symbol}  {o.direction}  {hold}")
    return "\n".join(rows)


def _pending_section(orders: list[V2PaperOrder]) -> str:
    if not orders:
        return "\n📋 当前挂单\n共 0 笔"
    rows = [f"\n📋 当前挂单\n共 {len(orders)} 笔"]
    for o in orders:
        rows.append(f"  {o.symbol}  {o.direction}  entry={o.entry}")
    return "\n".join(rows)


def _recent_settled_section(orders: list[V2PaperOrder]) -> str:
    if not orders:
        return "\n✅ 最近结算\n暂无"
    rows = [f"\n✅ 最近结算\n最近 {len(orders)} 笔"]
    for o in orders:
        pnl = _pnl_str(o.pnl_pct)
        dur = _dur_str(o.duration_minutes)
        rows.append(f"  {o.symbol}  {o.direction}  {o.result}  {pnl}  {dur}")
    return "\n".join(rows)
