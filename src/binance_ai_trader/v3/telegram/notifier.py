"""V3 Telegram Notifier — candidate push messages with Signal ID.

Format (fixed, do not change without explicit instruction):

  [V3] 🔥 Hotlist
  ━━━━━━━━━━━━━━
  Signal   HOT-20260704-000001
  BTCUSDT  LONG
  Entry    100.00
  SL       95.00   (-5.0%)
  TP1      110.00  (+10.0%)
  RR       2.0
  Regime   BULL
  Reason   Hotlist momentum breakout
  有效期   24h
"""
from __future__ import annotations

import logging
from decimal import Decimal

from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v3.candidates.repository import V3Candidate

log = logging.getLogger(__name__)

_STRATEGY_LABELS: dict[str, str] = {
    "hotlist_momentum_v3": "🔥 Hotlist",
    "hotlist_v66":         "📡 V66 Watchlist",
    "monster_v3":          "👾 Monster",
    "breakout_v3":         "📈 Breakout",
    "bear_v3":             "🐻 Bear",
}


def _label(strategy_id: str) -> str:
    return _STRATEGY_LABELS.get(strategy_id, f"📊 {strategy_id}")


def _pct(raw: str, ref: str) -> str:
    try:
        v = (Decimal(raw) - Decimal(ref)) / Decimal(ref) * 100
        sign = "+" if v >= 0 else ""
        return f"({sign}{v:.1f}%)"
    except Exception:
        return ""


class V3TelegramNotifier:
    def __init__(self, notifier: TelegramNotifier) -> None:
        self._notifier = notifier

    def send_candidate(
        self,
        candidate: V3Candidate,
        hold_hours: int = 24,
        live_prefix: str | None = None,
    ) -> None:
        msg = _format_candidate(candidate, hold_hours, live_prefix=live_prefix)
        try:
            self._notifier.send(msg)
            log.info("[V3] candidate sent: %s", candidate.signal_id)
        except Exception:
            log.exception("[V3] failed to send candidate %s", candidate.signal_id)
            raise

    def send(self, text: str) -> None:
        self._notifier.send(text)


def _format_candidate(
    c: V3Candidate,
    hold_hours: int,
    live_prefix: str | None = None,
) -> str:
    sl_pct  = _pct(c.sl,  c.entry)
    tp1_pct = _pct(c.tp1, c.entry)
    regime  = f"\nRegime   {c.market_regime}" if c.market_regime else ""
    reason  = f"\nReason   {c.reason}"        if c.reason        else ""
    prefix  = f"{live_prefix}\n" if live_prefix else ""

    return (
        f"{prefix}"
        f"[V3] {_label(c.strategy_id)}\n"
        f"━━━━━━━━━━━━━━\n"
        f"Signal   {c.signal_id}\n"
        f"{c.symbol}  {c.direction}\n"
        f"Entry    {c.entry}\n"
        f"SL       {c.sl}  {sl_pct}\n"
        f"TP1      {c.tp1}  {tp1_pct}\n"
        f"RR       {c.rr}"
        f"{regime}"
        f"{reason}\n"
        f"有效期   {hold_hours}h"
    )
