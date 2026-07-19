"""Unified signal scoring data models — SCORE_V1_UNIFIED.

Total 100 pts:
  volume_score          0–30  量能与流动性
  trend_structure_score 0–25  多周期趋势结构
  entry_position_score  0–20  入场位置评估
  risk_reward_score     0–15  风险收益质量
  strategy_fit_score    0–10  策略条件匹配度

Grade thresholds:
  A  85–100  优质
  B  70–84   良好
  C  55–69   一般
  D  0–54    较弱

score_version = SCORE_V1_UNIFIED
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

SCORE_VERSION = "SCORE_V1_UNIFIED"

GRADE_LABELS: dict[str, str] = {
    "A": "优质",
    "B": "良好",
    "C": "一般",
    "D": "较弱",
}

GRADE_EMOJI: dict[str, str] = {
    "A": "🏆",
    "B": "⭐",
    "C": "📊",
    "D": "💤",
}

MAX_SCORES = {
    "volume_score":          30,
    "trend_structure_score": 25,
    "entry_position_score":  20,
    "risk_reward_score":     15,
    "strategy_fit_score":    10,
}


def score_grade(total: int) -> str:
    if total >= 85:
        return "A"
    if total >= 70:
        return "B"
    if total >= 55:
        return "C"
    return "D"


def rr_score_pts(stop_pct: float, rr: float) -> int:
    """Compute risk_reward_score (0–15) from stop_pct and rr.

    Standalone so it can be imported by telegram_push.py without circular deps.
    """
    # Stop quality: 0–7 pts
    if 1.5 <= stop_pct <= 3.0:
        sp_pts = 7
    elif 3.0 < stop_pct <= 5.0:
        sp_pts = 5
    elif 0.8 <= stop_pct < 1.5:
        sp_pts = 4
    elif stop_pct > 5.0:
        sp_pts = 3
    elif stop_pct > 0:
        sp_pts = 2
    else:
        sp_pts = 0

    # RR quality: 0–8 pts
    if rr >= 3.0:
        rr_pts = 8
    elif rr >= 2.5:
        rr_pts = 7
    elif rr >= 2.0:
        rr_pts = 6
    elif rr >= 1.5:
        rr_pts = 4
    else:
        rr_pts = 2

    return min(15, sp_pts + rr_pts)


@dataclass
class UnifiedScore:
    score_total: int
    score_grade: str
    score_version: str = SCORE_VERSION
    volume_score: int = 0
    trend_structure_score: int = 0
    entry_position_score: int = 0
    risk_reward_score: int = 0
    strategy_fit_score: int = 0
    score_summary: str = ""
    score_details: dict = field(default_factory=dict)
    scored_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
