"""V2 Hourly Shadow Report — [V2] Hotlist Paper

Fixed format (do not change without explicit instruction):

  📊 V2 Hotlist Paper

  【累计（All Time）】
  【今日（Today）】
  【当前挂单（Pending）】
  【当前持仓（Filled）】
  【最近结算（最近7笔）】
  【System】
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v2.paper_portfolio.repository import V2PaperOrder, V2PaperOrderRepository
from binance_ai_trader.v2.performance.calculator import (
    V2Performance,
    V2PerformanceCalculator,
    V2TodayStats,
)

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


def _pnl_str(pnl: Decimal | None) -> str:
    if pnl is None:
        return "—"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.2f}%"


def _dur_str(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def _short_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    return iso[:16].replace("T", " ")


def _current_pnl(
    order: V2PaperOrder,
    price_map: dict[str, Decimal],
) -> str:
    price = price_map.get(order.symbol)
    if price is None:
        return "—"
    try:
        if order.direction == "LONG":
            pnl = (price - order.entry) / order.entry * Decimal("100")
        else:
            pnl = (order.entry - price) / order.entry * Decimal("100")
        return _pnl_str(pnl.quantize(Decimal("0.01")))
    except Exception:
        return "—"


def _fetch_prices(
    client: BinancePublicClient | None,
    symbols: list[str],
) -> dict[str, Decimal]:
    if client is None or not symbols:
        return {}
    try:
        tickers = client.tickers_24h()
        return {
            t.symbol: t.last_price
            for t in tickers
            if t.symbol in symbols
        }
    except Exception:
        return {}


class V2ShadowReporter:
    def __init__(
        self,
        notifier: TelegramNotifier,
        order_repo: V2PaperOrderRepository,
        perf_calc: V2PerformanceCalculator,
        strategy_id: str,
        client: BinancePublicClient | None = None,
        scan_interval_minutes: int = 15,
        settle_interval_minutes: int = 15,
        summary_interval_hours: int = 1,
    ) -> None:
        self._notifier = notifier
        self._order_repo = order_repo
        self._perf_calc = perf_calc
        self._strategy_id = strategy_id
        self._client = client
        self._scan_min = scan_interval_minutes
        self._settle_min = settle_interval_minutes
        self._summary_h = summary_interval_hours

    def send_report(self) -> None:
        try:
            msg = self._build_message()
            self._notifier.send(msg)
            log.info("[V2] hourly shadow report sent")
        except Exception:
            log.exception("[V2] failed to send shadow report")
            raise

    def _build_message(self) -> str:
        perf    = self._perf_calc.calculate(self._strategy_id)
        today   = self._perf_calc.calculate_today(self._strategy_id)
        open_orders     = self._order_repo.load_open()
        recent_settled  = self._order_repo.load_recent_settled(7)

        filled_orders  = [o for o in open_orders if o.status == "FILLED"]
        pending_orders = [o for o in open_orders if o.status == "OPEN"]

        filled_symbols = [o.symbol for o in filled_orders]
        price_map = _fetch_prices(self._client, filled_symbols)

        parts: list[str] = []
        parts.append("📊 V2 Hotlist Paper\n")
        parts.append(_alltime_section(perf))
        parts.append(_today_section(today))
        parts.append(_pending_section(pending_orders))
        parts.append(_positions_section(filled_orders, price_map))
        parts.append(_recent_settled_section(recent_settled))
        parts.append(_system_section(self._scan_min, self._settle_min, self._summary_h))

        return "\n".join(parts)


def _alltime_section(perf: V2Performance) -> str:
    return (
        "【累计（All Time）】\n"
        f"Signals：     {perf.signals}\n"
        f"Pushed：      {perf.pushed}\n"
        f"Orders：      {perf.orders}\n"
        f"Filled：      {perf.filled}\n"
        f"Settled：     {perf.settled}\n"
        "\n"
        f"TP1：         {perf.tp1}\n"
        f"TP2：         {perf.tp2}\n"
        f"SL：          {perf.sl}\n"
        f"EXPIRED：     {perf.expired_not_filled}\n"
        "\n"
        f"Win Rate：    {perf.win_rate}%\n"
        f"Average RR：  {perf.avg_rr}\n"
        f"Average PnL： {perf.avg_pnl}%"
    )


def _today_section(today: V2TodayStats) -> str:
    return (
        "\n【今日（Today）】\n"
        f"Signals：  {today.signals}\n"
        f"Pushed：   {today.pushed}\n"
        f"Filled：   {today.filled}\n"
        f"Settled：  {today.settled}\n"
        f"TP1：      {today.tp1}\n"
        f"TP2：      {today.tp2}\n"
        f"SL：       {today.sl}"
    )


def _pending_section(orders: list[V2PaperOrder]) -> str:
    if not orders:
        return "\n【当前挂单（Pending）】\n共 0 笔"
    rows = [f"\n【当前挂单（Pending）】\n共 {len(orders)} 笔"]
    for o in orders:
        rows.append(
            f"  {o.symbol} {o.direction}\n"
            f"  Entry={o.entry}  创建={_short_dt(o.created_at)}  到期={_short_dt(o.expires_at)}"
        )
    return "\n".join(rows)


def _positions_section(
    orders: list[V2PaperOrder],
    price_map: dict[str, Decimal],
) -> str:
    if not orders:
        return "\n【当前持仓（Filled）】\n共 0 笔"
    rows = [f"\n【当前持仓（Filled）】\n共 {len(orders)} 笔"]
    for o in orders:
        pnl  = _current_pnl(o, price_map)
        hold = _hold_duration(o.filled_at)
        rows.append(
            f"  {o.symbol} {o.direction}\n"
            f"  Entry={o.entry}  PnL={pnl}  持仓={hold}"
        )
    return "\n".join(rows)


def _recent_settled_section(orders: list[V2PaperOrder]) -> str:
    if not orders:
        return "\n【最近结算（最近7笔）】\n暂无"
    rows = [f"\n【最近结算（最近7笔）】\n共 {len(orders)} 笔"]
    for o in orders:
        pnl = _pnl_str(o.pnl_pct)
        dur = _dur_str(o.duration_minutes)
        rows.append(f"  {o.symbol} {o.direction}  {o.result}  {pnl}  {dur}")
    return "\n".join(rows)


def _system_section(
    scan_min: int,
    settle_min: int,
    summary_h: int,
) -> str:
    return (
        "\n【System】\n"
        f"Scan：    每 {scan_min} 分钟\n"
        f"Settle：  每 {settle_min} 分钟\n"
        f"Summary： 每 {summary_h} 小时"
    )
