"""Shared strategy_id -> Telegram tag/label mapping.

Every push message (candidate, settlement, weekly review, hourly report,
shadow report, startup) must show which strategy it belongs to. Never
hardcode "[V3]" for a message that might actually be about another
strategy (e.g. V66) — always look the tag up from strategy_id.
"""
from __future__ import annotations

_STRATEGY_TAGS: dict[str, str] = {
    "hotlist_momentum_v3": "V3",
    "hotlist_v66":         "V66",
    "monster_v3":          "V3",
    "breakout_v3":         "V3",
    "bear_v3":             "V3",
    "hotlist_reversal":    "REV",
}

_STRATEGY_LABELS: dict[str, str] = {
    "hotlist_momentum_v3": "🔥 Hotlist",
    "hotlist_v66":         "📡 V66 Watchlist",
    "monster_v3":          "👾 Monster",
    "breakout_v3":         "📈 Breakout",
    "bear_v3":             "🐻 Bear",
    "hotlist_reversal":    "🪤 V-Reversal",
}


def strategy_tag(strategy_id: str | None) -> str:
    """Short bracket tag, e.g. 'V3' or 'V66', for use as `[{tag}]`."""
    if not strategy_id:
        return "V3"
    return _STRATEGY_TAGS.get(strategy_id, strategy_id)


def strategy_label(strategy_id: str | None) -> str:
    """Longer descriptive label, e.g. '🔥 Hotlist' or '📡 V66 Watchlist'."""
    if not strategy_id:
        return "📊 unknown"
    return _STRATEGY_LABELS.get(strategy_id, f"📊 {strategy_id}")
