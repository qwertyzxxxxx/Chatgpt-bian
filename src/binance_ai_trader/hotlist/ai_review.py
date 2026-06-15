from __future__ import annotations

import re
from collections.abc import Sequence

from binance_ai_trader.hotlist.models import AIHotlistDecision, HotlistEntryPlan


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
