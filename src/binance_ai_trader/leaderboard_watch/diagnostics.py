"""Pure field-quality analyzer for leaderboard-watch Gemini candidates.

Computes how complete the JSON sent to Gemini is, so the operator can see *why*
the AI keeps returning NO_TRADE without lowering any decision thresholds. The
functions here are pure (no I/O) and operate on candidate dicts produced by
``WatchCandidateForGemini.to_dict()``.
"""
from __future__ import annotations

from typing import Any

EXPECTED_TIMEFRAMES: tuple[str, ...] = ("m15", "h1", "h4", "d1")
EXPECTED_TF_FIELDS: tuple[str, ...] = (
    "trend",
    "rsi14",
    "atr_pct",
    "volume_ratio",
    "recent_high",
    "recent_low",
)
EXPECTED_TOP_FIELDS: tuple[str, ...] = (
    "symbol",
    "rank_type",
    "change_24h",
    "quote_volume",
)

# A candidate dict may carry the canonical field name or an alias (the build
# pipeline historically used latest_* prefixes for some top-level fields).
_TOP_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol",),
    "rank_type": ("rank_type", "latest_rank_type"),
    "change_24h": ("change_24h", "latest_change_24h"),
    "quote_volume": ("quote_volume",),
}

_UNKNOWN_VALUES: frozenset[str] = frozenset({"", "UNKNOWN", "N/A", "NONE", "NULL"})


def _is_unknown(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().upper() in _UNKNOWN_VALUES


def _missing_top_field(candidate: dict[str, Any], field: str) -> bool:
    for key in _TOP_FIELD_ALIASES.get(field, (field,)):
        if key in candidate and not _is_unknown(candidate[key]):
            return False
    return True


def _classify_risk(candidate: dict[str, Any]) -> str:
    """Volatility-based risk proxy from the 1h ATR%.

    UNKNOWN volatility is treated as HIGH (uncertainty is risk). This is a
    descriptive classification for the diagnostic only; it never changes any
    trade decision.
    """
    h1 = candidate.get("h1")
    atr = h1.get("atr_pct") if isinstance(h1, dict) else None
    if _is_unknown(atr):
        return "HIGH"
    try:
        v = abs(float(atr))
    except (TypeError, ValueError):
        return "HIGH"
    if v >= 5.0:
        return "HIGH"
    if v >= 2.0:
        return "MEDIUM"
    return "LOW"


def analyze_candidate_fields(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Return field-completeness statistics for a list of candidate dicts."""
    candidate_count = len(candidates)
    total_fields = 0
    unknown_fields = 0
    missing_field_counts: dict[str, int] = {}
    risk_distribution: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    for candidate in candidates:
        for field in EXPECTED_TOP_FIELDS:
            total_fields += 1
            if _missing_top_field(candidate, field):
                unknown_fields += 1
                missing_field_counts[field] = missing_field_counts.get(field, 0) + 1

        for timeframe in EXPECTED_TIMEFRAMES:
            tf_data = candidate.get(timeframe)
            if not isinstance(tf_data, dict):
                tf_data = {}
            for field in EXPECTED_TF_FIELDS:
                total_fields += 1
                if field not in tf_data or _is_unknown(tf_data.get(field)):
                    unknown_fields += 1
                    key = f"{timeframe}.{field}"
                    missing_field_counts[key] = missing_field_counts.get(key, 0) + 1

        risk_distribution[_classify_risk(candidate)] += 1

    unknown_ratio = round(unknown_fields / total_fields, 4) if total_fields else 0.0
    return {
        "candidate_count": candidate_count,
        "total_fields": total_fields,
        "unknown_fields": unknown_fields,
        "unknown_ratio": unknown_ratio,
        "missing_field_counts": missing_field_counts,
        "risk_distribution": risk_distribution,
    }


def top_missing_fields(
    missing_field_counts: dict[str, int], n: int = 5
) -> list[tuple[str, int]]:
    """Return the top-N missing/UNKNOWN fields by count (ties sorted by name)."""
    return sorted(missing_field_counts.items(), key=lambda x: (-x[1], x[0]))[:n]
