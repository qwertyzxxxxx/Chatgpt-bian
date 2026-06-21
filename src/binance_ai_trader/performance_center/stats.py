from __future__ import annotations

from typing import List

from .models import (
    StrategyResult, StrategyStats, Leaderboard,
    STRATEGY_HOTLIST, STRATEGY_AI_MACRO, STRATEGY_GEMINI,
    RESULT_TP1, RESULT_TP2, RESULT_SL, RESULT_TIMEOUT, RESULT_OPEN,
    WIN_RESULTS,
)

ALL_STRATEGIES = (STRATEGY_HOTLIST, STRATEGY_AI_MACRO, STRATEGY_GEMINI)

_CORE_STRATEGIES = frozenset(ALL_STRATEGIES)

_KNOWN_SIGNAL_STRATEGIES = (
    "baseline_v1",
    "breakout_hunter_v1",
    "bear_short_space80_v1",
    "capital_60_80_space80_v1",
    "range_disabled_v1",
)


def _consecutive(results: List[str]) -> tuple:
    max_wins = max_losses = cur_wins = cur_losses = 0
    for r in results:
        if r in WIN_RESULTS:
            cur_wins += 1
            cur_losses = 0
        elif r in (RESULT_SL, RESULT_TIMEOUT):
            cur_losses += 1
            cur_wins = 0
        else:
            cur_wins = 0
            cur_losses = 0
        max_wins = max(max_wins, cur_wins)
        max_losses = max(max_losses, cur_losses)
    return max_wins, max_losses


def compute_stats(results: List[StrategyResult], strategy: str) -> StrategyStats:
    items = [r for r in results if r.strategy == strategy]
    s = StrategyStats(strategy=strategy)
    s.total = len(items)
    s.tp1 = sum(1 for r in items if r.result == RESULT_TP1)
    s.tp2 = sum(1 for r in items if r.result == RESULT_TP2)
    s.sl = sum(1 for r in items if r.result == RESULT_SL)
    s.timeout = sum(1 for r in items if r.result == RESULT_TIMEOUT)
    s.open_count = sum(1 for r in items if r.result == RESULT_OPEN)

    closed = [r for r in items if r.result != RESULT_OPEN]
    if closed:
        wins = [r for r in closed if r.result in WIN_RESULTS]
        s.win_rate = round(len(wins) / len(closed) * 100, 1)
        rrs = [r.rr_realized for r in closed if r.rr_realized is not None]
        s.avg_rr = round(sum(rrs) / len(rrs), 2) if rrs else 0.0
        pnls = [r.pnl_pct for r in closed if r.pnl_pct is not None]
        s.avg_pnl_pct = round(sum(pnls) / len(pnls), 4) if pnls else 0.0
        result_seq = [r.result for r in closed]
        s.max_consecutive_wins, s.max_consecutive_losses = _consecutive(result_seq)

    return s


def compute_all_stats(results: List[StrategyResult]) -> List[StrategyStats]:
    return [compute_stats(results, s) for s in ALL_STRATEGIES]


def build_leaderboard(results: List[StrategyResult]) -> Leaderboard:
    core_stats = compute_all_stats(results)

    seen_signal = {r.strategy for r in results if r.strategy not in _CORE_STRATEGIES}
    signal_ids = [s for s in _KNOWN_SIGNAL_STRATEGIES if s in seen_signal]
    signal_ids += sorted(seen_signal - set(_KNOWN_SIGNAL_STRATEGIES))
    signal_stats = [compute_stats(results, s) for s in signal_ids]

    all_stats = core_stats + signal_stats
    sorted_stats = sorted(
        [s for s in all_stats if s.total > 0],
        key=lambda s: (s.win_rate, s.avg_rr),
        reverse=True,
    )
    return Leaderboard(entries=sorted_stats)
