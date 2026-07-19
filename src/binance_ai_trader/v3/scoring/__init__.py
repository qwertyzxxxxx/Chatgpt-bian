"""Unified signal scoring — SCORE_V1_UNIFIED.

Public API:
  score_signal(candidate, klines)            → UnifiedScore
  score_signal_with_client(candidate, client) → UnifiedScore | None
  format_score_block(score)                  → str (Telegram block)
"""
from binance_ai_trader.v3.scoring.engine import score_signal, score_signal_with_client
from binance_ai_trader.v3.scoring.formatter import format_score_block
from binance_ai_trader.v3.scoring.models import SCORE_VERSION, UnifiedScore, score_grade

__all__ = [
    "score_signal",
    "score_signal_with_client",
    "format_score_block",
    "UnifiedScore",
    "SCORE_VERSION",
    "score_grade",
]
