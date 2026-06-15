from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from binance_ai_trader.hotlist.models import (
    AIHotlistDecision,
    HotlistAIReview,
    HotlistEntryPlan,
)


_CONFIDENCE_ORDER = {"STRONG": 0, "MEDIUM": 1, "WEAK": 2}


def review_hotlist_opportunities(
    contexts: Sequence[HotlistEntryPlan], limit: int = 5
) -> tuple[HotlistAIReview, ...]:
    """Rank public-data research plans without calling an external AI service."""
    if not 1 <= limit <= 5:
        raise ValueError("limit must be between 1 and 5")
    reviews = []
    for plan in contexts:
        confidence = _confidence(plan)
        reviews.append(
            HotlistAIReview(
                symbol=plan.symbol,
                direction=plan.direction,
                entry=plan.suggested_limit_entry,
                stop_loss=plan.stop_loss,
                tp1=plan.tp1,
                tp2=plan.tp2,
                rr=plan.rr,
                confidence=confidence,
                reason=_review_reason(plan, confidence),
                expires_at=plan.expires_at,
            )
        )
    reviews.sort(
        key=lambda item: (
            _CONFIDENCE_ORDER[item.confidence],
            -item.rr,
            item.symbol,
        )
    )
    return tuple(reviews[:limit])


def _confidence(plan: HotlistEntryPlan) -> str:
    if (
        plan.rr >= Decimal("3")
        and plan.volume_ratio_15m >= Decimal("1.5")
        and abs(plan.change_24h_pct) >= Decimal("15")
    ):
        return "STRONG"
    if plan.rr >= Decimal("2") and plan.volume_ratio_15m >= Decimal("1"):
        return "MEDIUM"
    return "WEAK"


def _review_reason(plan: HotlistEntryPlan, confidence: str) -> str:
    return (
        f"{confidence} research setup: {plan.direction} momentum retest, "
        f"RR {plan.rr}, 15m volume ratio {plan.volume_ratio_15m}, "
        f"24h move {plan.change_24h_pct}%."
    )


def build_ai_hotlist_review_prompt(contexts: Sequence[HotlistEntryPlan]) -> str:
    lines = [
        "Review these research-only hotlist plans. Do not place trades.",
        "Reply one line per symbol as: SYMBOL: APPROVED|REJECTED - reason",
    ]
    for plan in contexts:
        lines.append(
            f"{plan.symbol} direction={plan.direction} entry={plan.suggested_limit_entry} "
            f"SL={plan.stop_loss} TP1={plan.tp1} TP2={plan.tp2} RR={plan.rr} "
            f"expiry={plan.expires_at}"
        )
    return "\n".join(lines)


def parse_ai_hotlist_review_response(text: str) -> tuple[AIHotlistDecision, ...]:
    decisions = []
    pattern = re.compile(
        r"^\s*([A-Z0-9]+)\s*:\s*(APPROVED|REJECTED)\s*(?:-\s*)?(.*)$",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        decisions.append(
            AIHotlistDecision(
                symbol=match.group(1).upper(),
                approved=match.group(2).upper() == "APPROVED",
                reason=match.group(3).strip(),
            )
        )
    return tuple(decisions)
