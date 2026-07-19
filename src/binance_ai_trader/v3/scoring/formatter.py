"""Unified score Telegram block formatter.

Appended to the bottom of every signal message when scoring succeeds.

Format:
  ━━━━━━━━━━━━━━━━
  综合评分：73/100｜⭐ B 良好

  量能：      22/30
  趋势结构：  18/25
  入场位置：  15/20
  风险收益：  12/15
  策略匹配：   6/10

  💬 点评：
  量能充沛，资金关注度高
  前方压力支撑较近，真实空间有限
"""
from __future__ import annotations

from binance_ai_trader.v3.scoring.models import GRADE_EMOJI, GRADE_LABELS, UnifiedScore


def format_score_block(score: UnifiedScore | None) -> str:
    """Return a Telegram score block string.  Empty string if score is None."""
    if score is None:
        return ""
    g     = score.score_grade
    emoji = GRADE_EMOJI.get(g, "📊")
    label = GRADE_LABELS.get(g, g)

    lines = [
        "",
        "━━━━━━━━━━━━━━━━",
        f"综合评分：{score.score_total}/100｜{emoji} {g} {label}",
        "",
        f"量能：      {score.volume_score}/30",
        f"趋势结构：  {score.trend_structure_score}/25",
        f"入场位置：  {score.entry_position_score}/20",
        f"风险收益：  {score.risk_reward_score}/15",
        f"策略匹配：  {score.strategy_fit_score}/10",
    ]
    if score.score_summary:
        lines += ["", "💬 点评：", score.score_summary]

    return "\n".join(lines)


def format_score_block_classic(score: UnifiedScore | None) -> str:
    """Same as format_score_block — alias for classic telegram_push.py clarity."""
    return format_score_block(score)
