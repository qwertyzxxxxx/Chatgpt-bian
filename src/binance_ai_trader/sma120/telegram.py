"""Telegram message formatter for SMA120 V1.9-D signals."""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.sma120.strategy import SMA120Signal


def send_sma120_signal(notifier: TelegramNotifier, signal: SMA120Signal, signal_id: str) -> None:
    """Send a new SMA120 V1.9-D signal notification to Telegram."""
    arrow  = "🟢 LONG" if signal.direction == "LONG" else "🔴 SHORT"
    sl_pts = f"-${abs(signal.stop_loss - signal.entry):.2f}"
    tp_pts = f"+${abs(signal.tp1       - signal.entry):.2f}"

    msg = (
        f"[SMA120 V1.9-D] 📡 新信号\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"XAUUSDT   {arrow}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"入场   {signal.entry:.2f}\n"
        f"止损   {signal.stop_loss:.2f}  ({sl_pts})\n"
        f"止盈   {signal.tp1:.2f}  ({tp_pts})\n"
        f"RR     1:{int(signal.rr)}\n"
        f"──────────────────\n"
        f"M5 ATR   {signal.m5_atr:.2f}\n"
        f"EMA20    {signal.m5_ema20:.2f}\n"
        f"SMA120   {signal.m5_sma120:.2f}\n"
        f"ID       {signal_id}"
    )
    notifier.send(msg)
