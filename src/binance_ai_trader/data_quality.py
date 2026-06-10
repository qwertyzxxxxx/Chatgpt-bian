from __future__ import annotations

from collections.abc import Mapping


DATA_QUALITY_STATUSES = ("COMPLETE", "PARTIAL", "STALE", "FALLBACK", "MISSING")
_PRECEDENCE = {status: index for index, status in enumerate(DATA_QUALITY_STATUSES)}


def validate_quality_status(status: str) -> str:
    if status not in _PRECEDENCE:
        raise ValueError(f"unsupported data quality status: {status}")
    return status


def worst_quality(*statuses: str) -> str:
    if not statuses:
        return "MISSING"
    validated = tuple(validate_quality_status(status) for status in statuses)
    return max(validated, key=_PRECEDENCE.__getitem__)


def quality_context_status(context: Mapping[str, str]) -> str:
    return worst_quality(*context.values())
