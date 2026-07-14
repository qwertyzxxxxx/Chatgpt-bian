"""Telegram push formatter for Classic C1-C4 signals."""
from __future__ import annotations

from decimal import Decimal

from binance_ai_trader.notifications import TelegramNotifier


_VOL_GRADE_EMOJI = {
    "S_PLUS":    "🔥🔥",
    "S":         "🔥",
    "A":         "⭐",
    "NORMAL":    "📊",
    "WEAK":      "💤",
    "EXHAUSTION":"⚡",
}

_DIRECTION_EMOJI = {
    "LONG":  "📗 做多",
    "SHORT": "📕 做空",
}


def _pct(v) -> str:
    return f"{float(v):+.2f}%"


def _price(v) -> str:
    f = float(v)
    if f >= 100:
        return f"{f:.2f}"
    if f >= 1:
        return f"{f:.4f}"
    return f"{f:.6f}"


def send_classic_signal(notifier: TelegramNotifier, sig: dict) -> None:
    """Send a formatted Classic strategy signal to Telegram."""
    direction_label = _DIRECTION_EMOJI.get(sig["direction"], sig["direction"])
    vol_em = _VOL_GRADE_EMOJI.get(sig.get("vol_grade", ""), "")
    entry   = sig["entry"]
    sl      = sig["sl"]
    tp1     = sig["tp1"]
    tp2     = sig["tp2"]
    rr      = sig["rr"]
    stop_pct = sig.get("stop_pct", 0)

    msg = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"【{sig['strategy_id'].upper()} {sig['strategy_name']}】📋 模拟盘\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 {sig['symbol']}  {direction_label}\n"
        f"📦 池: {sig['pool_type']} #{sig['pool_rank']}\n"
        f"\n"
        f"💰 入场: {_price(entry)}\n"
        f"🛑 止损: {_price(sl)}  ({float(stop_pct):.1f}%)\n"
        f"🎯 TP1:  {_price(tp1)}  ({float(rr):.1f}R)\n"
        f"🎯 TP2:  {_price(tp2)}\n"
        f"\n"
        f"📊 量价等级: {vol_em} {sig.get('vol_grade','')}\n"
        f"   1H量比: {float(sig.get('vol_ratio_1h',0)):.2f}x\n"
        f"   15m量比: {float(sig.get('vol_ratio_15m',0)):.2f}x\n"
        f"\n"
        f"📈 3日涨跌: {_pct(sig.get('change_3d',0))}\n"
        f"📈 7日涨跌: {_pct(sig.get('change_7d',0))}\n"
        f"📈 24h涨跌: {_pct(sig.get('change_24h',0))}\n"
        f"📍 30日位置: {float(sig.get('range_pos_30d',0)):.2f}\n"
        f"📅 连续天数: {sig.get('consec_days',0)}天\n"
        f"📏 距4H EMA20: {float(sig.get('dist_4h_ema_atr',0)):.2f} ATR\n"
        f"\n"
        f"🔍 图形: {sig.get('pattern_desc','')}\n"
        f"✅ 禁止条件: {sig.get('block_checks','OK')}\n"
        f"🏆 评分: {sig.get('score',0)}/100\n"
    )
    notifier.send(msg)
