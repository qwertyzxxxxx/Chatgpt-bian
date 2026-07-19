"""V3 Telegram Notifier — candidate push messages with Signal ID.

Format:

  [V3] 🔥 Hotlist
  ━━━━━━━━━━━━━━
  Signal   HOT-20260704-000001
  BTCUSDT  LONG
  Entry    100.00
  SL       95.00   (-5.0%)
  TP1      105.00  (+5.0%)   ← 1:1 半仓减仓参考
  TP2      110.00  (+10.0%)  ← 实际止盈目标 (RR 2.0)
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
from binance_ai_trader.v3.telegram.labels import strategy_label, strategy_tag

log = logging.getLogger(__name__)

_SCORING_AVAILABLE: bool
try:
    from binance_ai_trader.v3.scoring import format_score_block, score_signal_with_client
    _SCORING_AVAILABLE = True
except Exception:
    _SCORING_AVAILABLE = False


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
        client=None,
    ) -> None:
        score = None
        if client is not None and _SCORING_AVAILABLE:
            score = score_signal_with_client(candidate, client)
        msg = _format_candidate(candidate, hold_hours, live_prefix=live_prefix, score=score)
        try:
            self._notifier.send(msg)
            log.info("[V3] candidate sent: %s  score=%s", candidate.signal_id,
                     f"{score.score_total}/100 {score.score_grade}" if score else "N/A")
        except Exception:
            log.exception("[V3] failed to send candidate %s", candidate.signal_id)
            raise

    def send(self, text: str) -> None:
        self._notifier.send(text)


def _format_candidate(
    c: V3Candidate,
    hold_hours: int,
    live_prefix: str | None = None,
    score=None,
) -> str:
    sl_pct  = _pct(c.sl,  c.entry)
    tp1_pct = _pct(c.tp1, c.entry)
    tp2     = c.tp2 if c.tp2 else c.tp1
    tp2_pct = _pct(tp2, c.entry)
    regime  = f"\nRegime   {c.market_regime}" if c.market_regime else ""
    reason  = f"\nReason   {c.reason}"        if c.reason        else ""
    prefix  = f"{live_prefix}\n" if live_prefix else ""

    score_block = ""
    if _SCORING_AVAILABLE and score is not None:
        score_block = format_score_block(score)

    return (
        f"{prefix}"
        f"[{strategy_tag(c.strategy_id)}] {strategy_label(c.strategy_id)}\n"
        f"━━━━━━━━━━━━━━\n"
        f"Signal   {c.signal_id}\n"
        f"{c.symbol}  {c.direction}\n"
        f"Entry    {c.entry}\n"
        f"SL       {c.sl}  {sl_pct}\n"
        f"TP1      {c.tp1}  {tp1_pct}\n"
        f"TP2      {tp2}  {tp2_pct}\n"
        f"RR       {c.rr}"
        f"{regime}"
        f"{reason}\n"
        f"有效期   {hold_hours}h"
        f"{score_block}"
    )
