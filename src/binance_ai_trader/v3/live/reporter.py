"""Hourly Live Mirror status report sent to Telegram."""
from __future__ import annotations

import logging
from decimal import Decimal

from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v3.live.engine import LiveMirrorEngine
from binance_ai_trader.v3.live.repository import LiveOrderRepository

log = logging.getLogger(__name__)


class LiveHourlyReporter:
    def __init__(
        self,
        engine: LiveMirrorEngine,
        repo: LiveOrderRepository,
        notifier: TelegramNotifier,
    ) -> None:
        self._engine   = engine
        self._repo     = repo
        self._notifier = notifier

    def send_report(self) -> None:
        try:
            msg = self._build()
            self._notifier.send(msg)
        except Exception:
            log.exception("[Live] hourly report failed")

    def _build(self) -> str:
        status = self._engine.get_account_status()
        if "error" in status:
            return f"[V3 LIVE HOURLY]\n❌ 账户查询失败: {status['error']}"

        balance        = status.get("balance",        Decimal("0"))
        available      = status.get("available",      Decimal("0"))
        unrealized_pnl = status.get("unrealized_pnl", Decimal("0"))
        realized_pnl   = status.get("realized_pnl",   Decimal("0"))
        positions      = status.get("positions",      [])
        open_orders    = status.get("open_orders",    [])

        today_orders  = self._repo.today_order_count()
        today_filled  = self._repo.count_today_by_type("FILLED")
        today_tp      = self._repo.count_today_by_type("CLOSED_TP")
        today_sl      = self._repo.count_today_by_type("CLOSED_SL")
        today_manual  = self._repo.count_today_by_type("MANUAL_CLOSED")

        risk = "🟢 正常"
        if len(open_orders) >= 8:
            risk = "🟡 挂单接近上限"
        if len(positions) >= 4:
            risk = "🟡 持仓接近上限"

        u_sign = "+" if unrealized_pnl >= 0 else ""
        r_sign = "+" if realized_pnl   >= 0 else ""

        lines = [
            "[V3 LIVE HOURLY]",
            "━━━━━━━━━━━━━━",
            f"余额         {balance:.2f} USDT",
            f"可用余额     {available:.2f} USDT",
            f"当前挂单数   {len(open_orders)}",
            f"当前持仓数   {len(positions)}",
            f"今日已实现   {r_sign}{realized_pnl:.2f} USDT",
            f"未实现PnL    {u_sign}{unrealized_pnl:.2f} USDT",
            f"今日下单数   {today_orders}",
            f"今日成交数   {today_filled}",
            f"今日止盈     {today_tp}",
            f"今日止损     {today_sl}",
            f"手动平仓     {today_manual}",
            f"风险状态     {risk}",
        ]

        if positions:
            lines.append("─────────────")
            lines.append("当前持仓：")
            for p in positions:
                sym  = p.get("symbol", "?")
                side = "LONG" if Decimal(str(p.get("positionAmt", "0"))) > 0 else "SHORT"
                upnl = Decimal(str(p.get("unRealizedProfit", "0")))
                sign = "+" if upnl >= 0 else ""
                lines.append(f"  {sym} {side}  {sign}{upnl:.2f}U")

        return "\n".join(lines)
