"""V2 Telegram Notifier — only sends [V2]-prefixed messages.

Sends:
  1. New signal alerts: [V2] Hotlist Momentum Signal
  2. 6-hour portfolio summary: [V2] Paper Portfolio Summary

Never sends per-settlement messages.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal

from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v2.performance.calculator import V2Performance
from binance_ai_trader.v2.signals.repository import V2Signal

log = logging.getLogger(__name__)


class V2TelegramNotifier:
    def __init__(self, notifier: TelegramNotifier) -> None:
        self._notifier = notifier

    def send_signal(self, signal: V2Signal) -> None:
        direction_emoji = "🟢" if signal.direction == "LONG" else "🔴"
        msg = (
            f"[V2] Hotlist Momentum Signal\n"
            f"{direction_emoji} {signal.symbol} {signal.direction}\n\n"
            f"📌 Entry:     {signal.entry}\n"
            f"🛡 Stop Loss: {signal.stop_loss}\n"
            f"🎯 TP1:       {signal.tp1}\n"
            f"🎯 TP2:       {signal.tp2}\n"
            f"📊 RR:        {signal.rr}\n\n"
            f"💡 {signal.reason}"
        )
        try:
            self._notifier.send(msg)
            log.info("[V2] signal alert sent: %s %s", signal.symbol, signal.direction)
        except Exception as exc:
            log.warning("[V2] failed to send signal alert: %s", exc)

    def send_summary(self, perf: V2Performance) -> None:
        denom = perf.tp1 + perf.tp2 + perf.sl
        msg = (
            f"[V2] Paper Portfolio Summary\n\n"
            f"📋 Orders:    {perf.orders}\n"
            f"✅ Filled:    {perf.filled}\n"
            f"⏳ Open:      {perf.open_count}\n"
            f"❌ No Fill:   {perf.not_filled}\n\n"
            f"🏆 TP1:  {perf.tp1}   TP2: {perf.tp2}\n"
            f"📉 SL:   {perf.sl}\n\n"
            f"📊 Win Rate:  {perf.win_rate}%  (denominator: {denom})\n"
            f"📈 Avg RR:    {perf.avg_rr}\n"
            f"💰 Avg PnL:   {perf.avg_pnl}%"
        )
        try:
            self._notifier.send(msg)
            log.info("[V2] 6h summary sent")
        except Exception as exc:
            log.warning("[V2] failed to send summary: %s", exc)
