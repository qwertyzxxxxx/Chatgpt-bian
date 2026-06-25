"""8-strategy unified diagnostic engine.

Queries market_data.db (+ ai_macro.db) to produce per-strategy funnel stats,
status (ALIVE / WEAK / DEAD), and machine-readable breakpoint reasons.

No strategy logic is changed here — this is read-only analysis.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

STRATEGY_REGISTRY: dict[str, dict] = {
    "hotlist": {
        "name": "Hotlist 快速通道",
        "type": "hotlist",
    },
    "baseline_v1": {
        "name": "Baseline V1",
        "type": "scan",
    },
    "breakout_hunter_v1": {
        "name": "Breakout Hunter V1",
        "type": "scan",
    },
    "bear_short_space80_v1": {
        "name": "Bear Short Space80",
        "type": "scan",
        "regime_bias": "BEAR",
    },
    "capital_60_80_space80_v1": {
        "name": "Capital 60-80 Space80",
        "type": "scan",
    },
    "range_disabled_v1": {
        "name": "Range Disabled V1",
        "type": "scan",
    },
    "ai_macro": {
        "name": "AI Macro",
        "type": "ai_macro",
    },
    "gemini_committee": {
        "name": "Gemini Committee",
        "type": "gemini",
    },
}

_STATUS_ALIVE = "ALIVE"
_STATUS_WEAK = "WEAK"
_STATUS_DEAD = "DEAD"
_STATUS_SLEEPING = "SLEEPING"

# Per-strategy regime gating — mirrors config/strategies/*.json `enabled_regimes`.
# A strategy absent from this map runs in every regime (e.g. baseline_v1).
# When the current market regime is NOT in a strategy's set, the strategy is
# *expected* to produce nothing, so it is reported as SLEEPING [disabled_by_regime]
# instead of DEAD. This is reporting metadata only — no trading logic is affected.
STRATEGY_ENABLED_REGIMES: dict[str, tuple[str, ...]] = {
    "bear_short_space80_v1": ("BEAR",),
    "range_disabled_v1": ("BULL", "BEAR"),
}

# Per-strategy minimum directional space score — mirrors config `space_score_min`.
# When directional space data is missing/insufficient the signal generator falls
# back to space_score=50, which fails any min > 50, silently filtering out every
# candidate. Strategies listed here are therefore attributed to missing space
# data (insufficient_history) rather than a generic "filters_too_strict".
STRATEGY_SPACE_SCORE_MIN: dict[str, float] = {
    "breakout_hunter_v1": 80.0,
    "capital_60_80_space80_v1": 80.0,
    "bear_short_space80_v1": 80.0,
}


def _normalize_regime(regime: str | None) -> str | None:
    """Collapse raw regime labels into BULL / BEAR / RANGE (or None if unknown)."""
    if not regime:
        return None
    r = regime.strip().upper()
    if r in ("BULL", "STRONG_BULL", "WEAK_BULL"):
        return "BULL"
    if r in ("BEAR", "STRONG_BEAR", "WEAK_BEAR"):
        return "BEAR"
    if r in ("RANGE", "OBSERVE", "NEUTRAL", "SIDEWAYS", "CHOP"):
        return "RANGE"
    return r


def _regime_disabled(strategy_id: str, regime: str | None) -> bool:
    """True when the strategy is gated off in the current regime (enabled_regimes mismatch)."""
    enabled = STRATEGY_ENABLED_REGIMES.get(strategy_id)
    if not enabled:
        return False  # runs in every regime
    norm = _normalize_regime(regime)
    if norm is None:
        return False  # unknown regime → don't suppress
    return norm not in enabled


def _space_gated(strategy_id: str) -> bool:
    """True when the strategy's space_score_min rejects the missing-data fallback (50)."""
    return STRATEGY_SPACE_SCORE_MIN.get(strategy_id, 0.0) > 50.0


@dataclass
class StrategyStats:
    strategy_id: str
    strategy_name: str
    registered: bool = True

    # Funnel counts (all within the since window)
    universe: int = 0
    scored: int = 0
    snapshots: int = 0
    signals: int = 0
    trades: int = 0
    results: int = 0

    # Result breakdown
    open_count: int = 0
    tp1: int = 0
    tp2: int = 0
    sl: int = 0
    timeout: int = 0

    # Performance
    win_rate: float | None = None
    avg_rr: float | None = None

    # Diagnosis
    status: str = _STATUS_DEAD
    dead_reasons: list[str] = field(default_factory=list)
    last_run_at: str | None = None


# ─────────────────────────── helpers ────────────────────────────────────────

def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _safe_count(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        row = con.execute(sql, params).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _latest_combined_regime(con: sqlite3.Connection) -> str | None:
    try:
        row = con.execute(
            "SELECT combined_regime FROM market_regimes ORDER BY evaluated_at DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None
    except Exception:
        return None


def _latest_run_id(con: sqlite3.Connection, since: str) -> str | None:
    """Most recent SUCCEEDED collection run id within the window (None if none)."""
    if not _table_exists(con, "collection_runs"):
        return None
    try:
        row = con.execute(
            "SELECT id FROM collection_runs WHERE status='SUCCEEDED' AND started_at >= ?"
            " ORDER BY started_at DESC LIMIT 1",
            (since,),
        ).fetchone()
        return str(row[0]) if row else None
    except Exception:
        return None


def _space_data_missing(con: sqlite3.Connection, run_id: str | None) -> bool:
    """True when the run has no COMPLETE directional space data (→ fallback space_score=50)."""
    if run_id is None or not _table_exists(con, "space_snapshots"):
        return True
    complete = _safe_count(
        con,
        "SELECT COUNT(*) FROM space_snapshots"
        " WHERE run_id = ? AND UPPER(data_quality_status) = 'COMPLETE'",
        (run_id,),
    )
    return complete == 0


def _paper_account_paused(con: sqlite3.Connection) -> bool:
    """True when the most recent paper account state is PAUSED."""
    if not _table_exists(con, "paper_accounts"):
        return False
    try:
        row = con.execute(
            "SELECT mode FROM paper_accounts ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return bool(row) and str(row[0]).upper() == "PAUSED"
    except Exception:
        return False


def _signals_expired_unfilled(con: sqlite3.Connection, strategy_id: str, since: str) -> bool:
    """True when the strategy's signals were evaluated as EXPIRED (entry never touched)."""
    if not (
        _table_exists(con, "signal_evaluations")
        and _table_exists(con, "signals")
        and _table_exists(con, "analysis_snapshots")
    ):
        return False
    try:
        row = con.execute(
            """
            SELECT COUNT(*) FROM signal_evaluations se
            JOIN signals s ON s.run_id = se.signal_run_id AND s.symbol = se.symbol
            LEFT JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
            WHERE COALESCE(a.strategy_id, 'baseline_v1') = ?
              AND se.evaluated_at >= ?
              AND UPPER(se.result) IN ('EXPIRED', 'TIMEOUT')
            """,
            (strategy_id, since),
        ).fetchone()
        return bool(row) and int(row[0]) > 0
    except Exception:
        return False


def _refined_weak_reason(con: sqlite3.Connection, strategy_id: str, since: str) -> str:
    """Refine the "signals but no executed trades" reason for a scan strategy."""
    if _paper_account_paused(con):
        return "paper_account_paused"
    if _signals_expired_unfilled(con, strategy_id, since):
        return "entry_not_touched / expired"
    return "signals_not_traded"


def classify_scan_status(
    con: sqlite3.Connection,
    strategy_id: str,
    since: str,
    *,
    scored: int,
    signals: int,
    trades: int,
    latest_run: str | None,
    regime: str | None,
) -> tuple[str, list[str]]:
    """Unified status + reason classification for a scan strategy.

    Reporting metadata only — never changes trading thresholds or logic.
    Precedence: ALIVE → WEAK(signals_not_traded family) → SLEEPING(regime) →
    no_snapshots → WEAK(space_score_missing) → filters_too_strict / no_market_match.
    """
    if signals > 0 and trades > 0:
        return _STATUS_ALIVE, []
    if signals > 0 and trades == 0:
        return _STATUS_WEAK, [_refined_weak_reason(con, strategy_id, since)]
    if _regime_disabled(strategy_id, regime):
        return _STATUS_SLEEPING, ["disabled_by_regime"]
    snapshots = (
        _safe_count(
            con,
            "SELECT COUNT(*) FROM analysis_snapshots WHERE strategy_id = ? AND created_at >= ?",
            (strategy_id, since),
        )
        if _table_exists(con, "analysis_snapshots")
        else 0
    )
    if snapshots <= 0:
        return _STATUS_DEAD, ["no_snapshots"]
    if _space_gated(strategy_id) and _space_data_missing(con, latest_run):
        return _STATUS_WEAK, ["space_score_missing / insufficient_history"]
    if scored > 0:
        return _STATUS_DEAD, ["filters_too_strict"]
    return _STATUS_DEAD, ["no_market_match"]


def _result_breakdown(con: sqlite3.Connection, table: str, since: str,
                      strategy_col: str, strategy_val: str,
                      result_col: str = "result",
                      time_col: str = "evaluated_at") -> dict[str, int]:
    if not _table_exists(con, table):
        return {}
    try:
        rows = con.execute(
            f"SELECT UPPER({result_col}), COUNT(*) FROM {table}"
            f" WHERE {strategy_col} = ? AND {time_col} >= ? GROUP BY UPPER({result_col})",
            (strategy_val, since),
        ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}
    except Exception:
        return {}


# ─────────────────────────── per-type queries ───────────────────────────────

def _query_scan_strategy(
    con: sqlite3.Connection,
    strategy_id: str,
    meta: dict,
    since: str,
) -> StrategyStats:
    stats = StrategyStats(
        strategy_id=strategy_id,
        strategy_name=meta["name"],
    )

    # Universe + scored (shared across strategies — use most recent run in window)
    latest_run: str | None = None
    if _table_exists(con, "collection_runs") and _table_exists(con, "universe_snapshots"):
        row = con.execute(
            "SELECT id FROM collection_runs WHERE status='SUCCEEDED' AND started_at >= ?"
            " ORDER BY started_at DESC LIMIT 1",
            (since,),
        ).fetchone()
        if row:
            latest_run = str(row[0])
            stats.universe = _safe_count(
                con, "SELECT COUNT(*) FROM universe_snapshots WHERE run_id = ?", (latest_run,)
            )
            stats.scored = _safe_count(
                con, "SELECT COUNT(*) FROM scores WHERE run_id = ?", (latest_run,)
            )

    # Snapshots for this strategy
    if _table_exists(con, "analysis_snapshots"):
        stats.snapshots = _safe_count(
            con,
            "SELECT COUNT(*) FROM analysis_snapshots WHERE strategy_id = ? AND created_at >= ?",
            (strategy_id, since),
        )
        row = con.execute(
            "SELECT MAX(created_at) FROM analysis_snapshots WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        stats.last_run_at = str(row[0]) if row and row[0] else None

    # Signals
    if _table_exists(con, "signals") and _table_exists(con, "analysis_snapshots"):
        stats.signals = _safe_count(
            con,
            """
            SELECT COUNT(*) FROM signals s
            LEFT JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
            WHERE COALESCE(a.strategy_id, 'baseline_v1') = ? AND s.generated_at >= ?
            """,
            (strategy_id, since),
        )

    # Trades (paper_trades linked via signals → analysis_snapshots)
    if (
        _table_exists(con, "paper_trades")
        and _table_exists(con, "signals")
        and _table_exists(con, "analysis_snapshots")
    ):
        stats.trades = _safe_count(
            con,
            """
            SELECT COUNT(*) FROM paper_trades pt
            JOIN signals s ON s.run_id = pt.signal_run_id AND s.symbol = pt.symbol
            LEFT JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
            WHERE COALESCE(a.strategy_id, 'baseline_v1') = ? AND pt.processed_at >= ?
            """,
            (strategy_id, since),
        )

    # strategy_results
    if _table_exists(con, "strategy_results"):
        stats.results = _safe_count(
            con,
            "SELECT COUNT(*) FROM strategy_results WHERE strategy = ? AND opened_at >= ?",
            (strategy_id, since),
        )
        breakdown = {}
        try:
            rows = con.execute(
                "SELECT UPPER(result), COUNT(*) FROM strategy_results"
                " WHERE strategy = ? AND opened_at >= ? GROUP BY UPPER(result)",
                (strategy_id, since),
            ).fetchall()
            breakdown = {str(r[0]): int(r[1]) for r in rows}
        except Exception:
            pass
        stats.open_count = breakdown.get("OPEN", 0)
        stats.tp1 = breakdown.get("TP1_HIT", 0)
        stats.tp2 = max(breakdown.get("WIN", 0), breakdown.get("TP2_HIT", 0), breakdown.get("WIN_TP2", 0))
        stats.sl = max(breakdown.get("LOSS", 0), breakdown.get("SL_HIT", 0))
        stats.timeout = breakdown.get("TIMEOUT", breakdown.get("EXPIRED", 0))

        closed = stats.tp1 + stats.tp2 + stats.sl + stats.timeout
        if closed > 0:
            wins = stats.tp1 + stats.tp2
            stats.win_rate = round(wins / closed * 100, 1)

        try:
            row = con.execute(
                "SELECT AVG(rr_realized) FROM strategy_results"
                " WHERE strategy = ? AND opened_at >= ? AND rr_realized IS NOT NULL",
                (strategy_id, since),
            ).fetchone()
            if row and row[0] is not None:
                stats.avg_rr = round(float(row[0]), 2)
        except Exception:
            pass

    # Status + reasons (reporting metadata only — no trading logic affected)
    stats.status, stats.dead_reasons = classify_scan_status(
        con,
        strategy_id,
        since,
        scored=stats.scored,
        signals=stats.signals,
        trades=stats.trades,
        latest_run=latest_run,
        regime=_latest_combined_regime(con),
    )

    return stats


def _query_hotlist_strategy(con: sqlite3.Connection, since: str) -> StrategyStats:
    stats = StrategyStats(
        strategy_id="hotlist",
        strategy_name=STRATEGY_REGISTRY["hotlist"]["name"],
    )

    if _table_exists(con, "hotlist_alerts"):
        stats.signals = _safe_count(
            con, "SELECT COUNT(*) FROM hotlist_alerts WHERE created_at >= ?", (since,)
        )
        row = con.execute(
            "SELECT MAX(created_at) FROM hotlist_alerts"
        ).fetchone()
        stats.last_run_at = str(row[0]) if row and row[0] else None

    if _table_exists(con, "hotlist_outcomes"):
        try:
            rows = con.execute(
                "SELECT UPPER(status), COUNT(*) FROM hotlist_outcomes"
                " WHERE evaluated_at >= ? GROUP BY UPPER(status)",
                (since,),
            ).fetchall()
            outcome_map = {str(r[0]): int(r[1]) for r in rows}
        except Exception:
            outcome_map = {}
        stats.open_count = outcome_map.get("OPEN", 0)
        stats.tp1 = outcome_map.get("TP1_HIT", 0)
        stats.tp2 = max(
            outcome_map.get("WIN", 0),
            outcome_map.get("TP2_HIT", 0),
            outcome_map.get("WIN_TP2", 0),
        )
        stats.sl = max(outcome_map.get("LOSS", 0), outcome_map.get("SL_HIT", 0))
        stats.timeout = outcome_map.get("TIMEOUT", outcome_map.get("EXPIRED", 0))
        stats.results = sum(outcome_map.values())

    if _table_exists(con, "strategy_results"):
        stats.trades = _safe_count(
            con,
            "SELECT COUNT(*) FROM strategy_results WHERE strategy = 'hotlist' AND opened_at >= ?",
            (since,),
        )
        closed = stats.tp1 + stats.tp2 + stats.sl + stats.timeout
        if closed > 0:
            wins = stats.tp1 + stats.tp2
            stats.win_rate = round(wins / closed * 100, 1)

    if stats.signals > 0 and (stats.trades > 0 or stats.results > 0):
        stats.status = _STATUS_ALIVE
    elif stats.signals > 0:
        stats.status = _STATUS_WEAK
        stats.dead_reasons = ["trades_not_created"]
    else:
        stats.status = _STATUS_DEAD
        stats.dead_reasons = ["no_signals"]

    return stats


def _query_ai_macro_strategy(ai_macro_db: str | None, since: str) -> StrategyStats:
    stats = StrategyStats(
        strategy_id="ai_macro",
        strategy_name=STRATEGY_REGISTRY["ai_macro"]["name"],
    )
    if not ai_macro_db:
        stats.dead_reasons = ["no_snapshots"]
        return stats

    try:
        con = sqlite3.connect(ai_macro_db)
        if _table_exists(con, "ai_macro_trades"):
            stats.signals = _safe_count(
                con,
                "SELECT COUNT(*) FROM ai_macro_trades WHERE created_at >= ?",
                (since,),
            )
            stats.trades = _safe_count(
                con,
                "SELECT COUNT(*) FROM ai_macro_trades"
                " WHERE created_at >= ? AND UPPER(status) NOT IN ('SKIPPED','PENDING','OPEN')",
                (since,),
            )
            stats.open_count = _safe_count(
                con,
                "SELECT COUNT(*) FROM ai_macro_trades"
                " WHERE created_at >= ? AND UPPER(status) = 'OPEN'",
                (since,),
            )
            try:
                rows = con.execute(
                    "SELECT UPPER(status), COUNT(*) FROM ai_macro_trades"
                    " WHERE created_at >= ? GROUP BY UPPER(status)",
                    (since,),
                ).fetchall()
                bk = {str(r[0]): int(r[1]) for r in rows}
                stats.tp1 = bk.get("TP1_HIT", 0)
                stats.tp2 = max(bk.get("WIN", 0), bk.get("WIN_TP2", 0))
                stats.sl = max(bk.get("LOSS", 0), bk.get("SL_HIT", 0))
                stats.results = stats.tp1 + stats.tp2 + stats.sl
                closed = stats.tp1 + stats.tp2 + stats.sl
                if closed > 0:
                    stats.win_rate = round((stats.tp1 + stats.tp2) / closed * 100, 1)
            except Exception:
                pass
            row = con.execute(
                "SELECT MAX(created_at) FROM ai_macro_trades"
            ).fetchone()
            stats.last_run_at = str(row[0]) if row and row[0] else None
        con.close()
    except Exception as exc:
        log.debug("ai_macro diagnostic failed: %s", exc)
        stats.dead_reasons = ["no_snapshots"]
        return stats

    if stats.signals > 0 and (stats.trades > 0 or stats.results > 0):
        stats.status = _STATUS_ALIVE
    elif stats.signals > 0 and stats.trades == 0 and stats.open_count > 0:
        stats.status = _STATUS_ALIVE
    elif stats.signals > 0:
        stats.status = _STATUS_WEAK
        stats.dead_reasons = ["signals_not_traded"]
    else:
        stats.status = _STATUS_DEAD
        stats.dead_reasons = ["no_signals"]

    return stats


def _query_gemini_strategy(con: sqlite3.Connection, since: str) -> StrategyStats:
    stats = StrategyStats(
        strategy_id="gemini_committee",
        strategy_name=STRATEGY_REGISTRY["gemini_committee"]["name"],
    )
    if not _table_exists(con, "gemini_committee_reviews"):
        stats.dead_reasons = ["no_snapshots"]
        return stats

    stats.signals = _safe_count(
        con,
        "SELECT COUNT(*) FROM gemini_committee_reviews WHERE created_at >= ?",
        (since,),
    )
    stats.trades = _safe_count(
        con,
        "SELECT COUNT(*) FROM gemini_committee_reviews"
        " WHERE created_at >= ? AND UPPER(decision) = 'TRADE'",
        (since,),
    )
    row = con.execute(
        "SELECT MAX(created_at) FROM gemini_committee_reviews"
    ).fetchone()
    stats.last_run_at = str(row[0]) if row and row[0] else None

    if stats.signals > 0 and stats.trades > 0:
        stats.status = _STATUS_ALIVE
    elif stats.signals > 0 and stats.trades == 0:
        stats.status = _STATUS_WEAK
        stats.dead_reasons = ["filters_too_strict"]
    else:
        stats.status = _STATUS_DEAD
        stats.dead_reasons = ["no_signals"]

    return stats


# ─────────────────────────── main entry point ───────────────────────────────

@dataclass
class FunnelLayer:
    name: str
    count: int
    note: str = ""


@dataclass
class FunnelStats:
    strategy_id: str
    strategy_name: str
    layers: list[FunnelLayer] = field(default_factory=list)
    new_coin_skipped: int = 0
    space_score_missing: int = 0
    space_score_ok: int = 0


def _count_new_coins_in_universe(con: sqlite3.Connection, run_id: str | None) -> int:
    """Count universe symbols with < 720 stored 4h klines (new coin criterion)."""
    if run_id is None or not _table_exists(con, "universe_snapshots"):
        return 0
    try:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM universe_snapshots WHERE run_id = ?", (run_id,)
        ).fetchall()
        symbols = [str(r[0]) for r in rows]
        new_count = 0
        for sym in symbols:
            row = con.execute(
                "SELECT COUNT(*) FROM klines WHERE symbol=? AND interval='4h'", (sym,)
            ).fetchone()
            cnt = int(row[0]) if row else 0
            if cnt < 720:
                new_count += 1
        return new_count
    except Exception:
        return 0


def _count_space_score_missing(con: sqlite3.Connection, run_id: str | None) -> int:
    """Universe symbols that lack a COMPLETE space snapshot in this run."""
    if run_id is None or not _table_exists(con, "space_snapshots"):
        return 0
    try:
        universe_count = _safe_count(
            con,
            "SELECT COUNT(DISTINCT symbol) FROM universe_snapshots WHERE run_id = ?",
            (run_id,),
        )
        space_complete = _safe_count(
            con,
            "SELECT COUNT(DISTINCT symbol) FROM space_snapshots"
            " WHERE run_id = ? AND UPPER(data_quality_status) = 'COMPLETE'",
            (run_id,),
        )
        return max(0, universe_count - space_complete)
    except Exception:
        return 0


def _count_space_score_ok(con: sqlite3.Connection, run_id: str | None, min_score: float) -> int:
    """Symbols with space_score >= min_score in this run."""
    if run_id is None or not _table_exists(con, "space_snapshots"):
        return 0
    try:
        return _safe_count(
            con,
            "SELECT COUNT(DISTINCT symbol) FROM space_snapshots"
            " WHERE run_id = ? AND CAST(space_score AS REAL) >= ?",
            (run_id, min_score),
        )
    except Exception:
        return 0


def _count_entry_touched(con: sqlite3.Connection, strategy_id: str, since: str) -> int:
    """Signals where price reached entry (EXPIRED or better outcome)."""
    if not (_table_exists(con, "signal_evaluations") and _table_exists(con, "signals")):
        return 0
    try:
        row = con.execute(
            """
            SELECT COUNT(*) FROM signal_evaluations se
            JOIN signals s ON s.run_id = se.signal_run_id AND s.symbol = se.symbol
            LEFT JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
            WHERE COALESCE(a.strategy_id, 'baseline_v1') = ?
              AND se.evaluated_at >= ?
              AND UPPER(se.result) NOT IN ('EXPIRED', 'TIMEOUT')
            """,
            (strategy_id, since),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _count_entry_not_touched(con: sqlite3.Connection, strategy_id: str, since: str) -> int:
    if not (_table_exists(con, "signal_evaluations") and _table_exists(con, "signals")):
        return 0
    try:
        row = con.execute(
            """
            SELECT COUNT(*) FROM signal_evaluations se
            JOIN signals s ON s.run_id = se.signal_run_id AND s.symbol = se.symbol
            LEFT JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
            WHERE COALESCE(a.strategy_id, 'baseline_v1') = ?
              AND se.evaluated_at >= ?
              AND UPPER(se.result) IN ('EXPIRED', 'TIMEOUT')
            """,
            (strategy_id, since),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def run_funnel_diagnostics(
    market_db: str,
    since_hours: int = 24,
) -> list[FunnelStats]:
    """Build per-strategy funnel layer counts from the market DB.

    Each strategy gets a ``FunnelStats`` with sequential filter layers showing
    how many symbols/signals survived each stage of the pipeline.
    """
    since = (datetime.now(UTC) - timedelta(hours=since_hours)).isoformat(timespec="seconds")
    results: list[FunnelStats] = []

    try:
        con = sqlite3.connect(market_db)
    except Exception as exc:
        log.error("Cannot open market_db for funnel: %s: %s", market_db, exc)
        return results

    try:
        latest_run = _latest_run_id(con, since)
        regime = _normalize_regime(_latest_combined_regime(con))

        # Shared universe + scored counts
        universe_count = (
            _safe_count(con, "SELECT COUNT(*) FROM universe_snapshots WHERE run_id = ?", (latest_run,))
            if latest_run else 0
        )
        scored_count = (
            _safe_count(con, "SELECT COUNT(*) FROM scores WHERE run_id = ?", (latest_run,))
            if latest_run else 0
        )
        new_coin_count = _count_new_coins_in_universe(con, latest_run)

        space_strategies = {"breakout_hunter_v1", "capital_60_80_space80_v1", "bear_short_space80_v1"}
        scan_strategies = [
            ("baseline_v1", "Baseline V1"),
            ("breakout_hunter_v1", "Breakout Hunter V1"),
            ("capital_60_80_space80_v1", "Capital 60-80 Space80"),
            ("bear_short_space80_v1", "Bear Short Space80"),
            ("range_disabled_v1", "Range Disabled V1"),
        ]

        for strategy_id, strategy_name in scan_strategies:
            fs = FunnelStats(strategy_id=strategy_id, strategy_name=strategy_name)
            is_space = strategy_id in space_strategies
            space_min = STRATEGY_SPACE_SCORE_MIN.get(strategy_id, 0.0)
            regime_allowed = not _regime_disabled(strategy_id, regime)

            signals_n = _safe_count(
                con,
                """
                SELECT COUNT(*) FROM signals s
                LEFT JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
                WHERE COALESCE(a.strategy_id, 'baseline_v1') = ? AND s.generated_at >= ?
                """,
                (strategy_id, since),
            ) if (_table_exists(con, "signals") and _table_exists(con, "analysis_snapshots")) else 0

            trades_n = _safe_count(
                con,
                """
                SELECT COUNT(*) FROM paper_trades pt
                JOIN signals s ON s.run_id = pt.signal_run_id AND s.symbol = pt.symbol
                LEFT JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
                WHERE COALESCE(a.strategy_id, 'baseline_v1') = ? AND pt.processed_at >= ?
                """,
                (strategy_id, since),
            ) if (
                _table_exists(con, "paper_trades")
                and _table_exists(con, "signals")
                and _table_exists(con, "analysis_snapshots")
            ) else 0

            results_n = _safe_count(
                con,
                "SELECT COUNT(*) FROM strategy_results WHERE strategy = ? AND opened_at >= ?",
                (strategy_id, since),
            ) if _table_exists(con, "strategy_results") else 0

            fs.layers.append(FunnelLayer("Universe", universe_count))
            fs.layers.append(FunnelLayer("Has enough Kline", scored_count,
                                         note=f"new_coin_skipped={new_coin_count}"))

            if regime_allowed:
                fs.layers.append(FunnelLayer("Regime allowed", scored_count))
            else:
                fs.layers.append(FunnelLayer("Regime allowed", 0,
                                             note=f"disabled_by_regime ({regime})"))

            if is_space:
                sp_missing = _count_space_score_missing(con, latest_run)
                sp_ok = _count_space_score_ok(con, latest_run, space_min)
                sp_note = (
                    f"space_score: MISSING={sp_missing}, reason=insufficient_4h_history"
                    f" | space_score>={space_min:.0f}: {sp_ok}"
                )
                fs.layers.append(FunnelLayer("Space filter", sp_ok, note=sp_note))
                fs.new_coin_skipped = new_coin_count
                fs.space_score_missing = sp_missing
                fs.space_score_ok = sp_ok
            else:
                fs.layers.append(FunnelLayer("Space filter", scored_count, note="no space_score_min"))

            fs.layers.append(FunnelLayer("Signals", signals_n))
            fs.layers.append(FunnelLayer("Paper trades", trades_n))
            fs.layers.append(FunnelLayer("Results", results_n))

            # baseline: detailed signal evaluation breakdown
            if strategy_id == "baseline_v1":
                touched = _count_entry_touched(con, strategy_id, since)
                not_touched = _count_entry_not_touched(con, strategy_id, since)
                paused = _paper_account_paused(con)
                fs.layers.append(FunnelLayer(
                    "Entry evaluation",
                    touched,
                    note=(
                        f"entry_touched={touched}"
                        f" | entry_not_touched/expired={not_touched}"
                        f" | paper_account_paused={paused}"
                    ),
                ))

            results.append(fs)
    finally:
        con.close()

    return results


def format_funnel_text(funnel_list: list[FunnelStats], since_hours: int = 24) -> str:
    """Format funnel diagnostics for CLI / Telegram."""
    now = datetime.now(UTC)
    sep = "─" * 60
    lines = [
        sep,
        f"  策略漏斗诊断  |  过去 {since_hours}h  |  {now.strftime('%Y-%m-%d %H:%M UTC')}",
        sep,
    ]
    for fs in funnel_list:
        lines.append(f"\n[{fs.strategy_id}]  {fs.strategy_name}")
        for lyr in fs.layers:
            note_str = f"  ← {lyr.note}" if lyr.note else ""
            lines.append(f"  ↓ {lyr.name}: {lyr.count}{note_str}")
        if fs.new_coin_skipped:
            lines.append(f"  ★ new_coin_skipped={fs.new_coin_skipped}")
        if fs.space_score_missing:
            lines.append(
                f"  ★ space_score: MISSING={fs.space_score_missing}"
                f", reason=insufficient_4h_history"
                f", space_score_ok={fs.space_score_ok}"
            )
    lines.append(f"\n{sep}")
    lines.append("仅供研究 | 不进行实盘交易")
    lines.append(sep)
    return "\n".join(lines)


def run_diagnostics(
    market_db: str,
    since_hours: int = 24,
    ai_macro_db: str | None = None,
) -> list[StrategyStats]:
    """Query all 8 strategies and return a list of StrategyStats."""
    since = (datetime.now(UTC) - timedelta(hours=since_hours)).isoformat(timespec="seconds")
    results: list[StrategyStats] = []

    try:
        con = sqlite3.connect(market_db)
    except Exception as exc:
        log.error("Cannot open market_db %s: %s", market_db, exc)
        for sid, meta in STRATEGY_REGISTRY.items():
            st = StrategyStats(strategy_id=sid, strategy_name=meta["name"])
            st.dead_reasons = ["no_snapshots"]
            results.append(st)
        return results

    try:
        for strategy_id, meta in STRATEGY_REGISTRY.items():
            stype = meta["type"]
            if stype == "scan":
                st = _query_scan_strategy(con, strategy_id, meta, since)
            elif stype == "hotlist":
                st = _query_hotlist_strategy(con, since)
            elif stype == "ai_macro":
                st = _query_ai_macro_strategy(ai_macro_db, since)
            elif stype == "gemini":
                st = _query_gemini_strategy(con, since)
            else:
                st = StrategyStats(strategy_id=strategy_id, strategy_name=meta["name"])
                st.dead_reasons = ["no_snapshots"]
            results.append(st)
    finally:
        con.close()

    return results


# ─────────────────────────── formatters ─────────────────────────────────────

def _status_emoji(status: str) -> str:
    return {"ALIVE": "✅", "WEAK": "⚠️", "DEAD": "💀", "SLEEPING": "😴"}.get(status, "❓")


def _fmt_win_rate(wr: float | None) -> str:
    return f"{wr:.1f}%" if wr is not None else "N/A"


def _fmt_avg_rr(rr: float | None) -> str:
    return f"{rr:.2f}R" if rr is not None else "N/A"


def format_telegram(
    stats_list: list[StrategyStats],
    since_hours: int = 24,
) -> str:
    """Format all 8 strategy diagnostics for Telegram."""
    now = datetime.now(UTC)
    lines = [
        "📊 8策略运行诊断",
        f"{now.strftime('%Y-%m-%d %H:%M UTC')}（过去{since_hours}小时）",
        "",
    ]
    for i, st in enumerate(stats_list, 1):
        emoji = _status_emoji(st.status)
        reason_str = " / ".join(st.dead_reasons) if st.dead_reasons else "无"
        lines.append(f"{i}. {st.strategy_name}")
        lines.append(f"   strategy_id: {st.strategy_id}")
        lines.append(f"   状态: {emoji} {st.status}")
        if st.snapshots > 0 or st.universe > 0:
            lines.append(
                f"   漏斗: universe={st.universe} → scored={st.scored}"
                f" → snapshots={st.snapshots} → signals={st.signals}"
                f" → paper_trades={st.trades}"
            )
        else:
            lines.append(f"   signals={st.signals} | paper_trades={st.trades}")
        lines.append(
            f"   结果: open={st.open_count} | TP1={st.tp1} | TP2={st.tp2}"
            f" | SL={st.sl} | TIMEOUT={st.timeout}"
        )
        lines.append(f"   win_rate={_fmt_win_rate(st.win_rate)} | avg_rr={_fmt_avg_rr(st.avg_rr)}")
        lines.append(f"   断点: {reason_str}")
        if st.last_run_at:
            lines.append(f"   最近运行: {st.last_run_at[:19]}")
        lines.append("")

    lines.append("仅供研究 | 不进行实盘交易")
    return "\n".join(lines)


def format_text(
    stats_list: list[StrategyStats],
    since_hours: int = 24,
) -> str:
    """Format all 8 strategy diagnostics for CLI stdout."""
    now = datetime.now(UTC)
    sep = "─" * 60
    lines = [
        sep,
        f"  8策略运行诊断  |  过去 {since_hours}h  |  {now.strftime('%Y-%m-%d %H:%M UTC')}",
        sep,
    ]
    for i, st in enumerate(stats_list, 1):
        emoji = _status_emoji(st.status)
        reason_str = " / ".join(st.dead_reasons) if st.dead_reasons else "—"
        lines.append(f"\n{i}. [{st.strategy_id}]  {st.strategy_name}  {emoji} {st.status}")
        lines.append(
            f"   漏斗:  universe={st.universe:>4}  scored={st.scored:>4}"
            f"  snapshots={st.snapshots:>4}  signals={st.signals:>4}"
            f"  paper_trades={st.trades:>4}"
        )
        lines.append(
            f"   结果:  open={st.open_count}  TP1={st.tp1}  TP2={st.tp2}"
            f"  SL={st.sl}  TIMEOUT={st.timeout}"
            f"   strategy_results={st.results}"
        )
        lines.append(
            f"   绩效:  win_rate={_fmt_win_rate(st.win_rate):>7}"
            f"  avg_rr={_fmt_avg_rr(st.avg_rr):>7}"
        )
        lines.append(f"   断点:  {reason_str}")
        if st.last_run_at:
            lines.append(f"   最近:  {st.last_run_at[:19]}")

    lines.append(f"\n{sep}")
    lines.append("仅供研究 | 不进行实盘交易")
    lines.append(sep)
    return "\n".join(lines)
