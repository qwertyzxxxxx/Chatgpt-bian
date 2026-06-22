from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger(__name__)

_KNOWN_SIGNAL_STRATEGIES = (
    "baseline_v1",
    "breakout_hunter_v1",
    "bear_short_space80_v1",
    "capital_60_80_space80_v1",
    "range_disabled_v1",
)

_BOTTLENECK_LABELS: dict[str, str] = {
    "A": "no analysis_snapshots — strategy never researched / auto-research not run",
    "B": "snapshots exist but no signals generated",
    "C": "signals exist but no signal_evaluations",
    "D": "evaluations exist but no paper_trades",
    "E": "paper_trades exist but not imported into strategy_results",
    "OK": "data present in strategy_results",
}


def _conn(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _safe_count_max(
    con: sqlite3.Connection, sql: str, params: tuple = ()
) -> tuple[int, str | None]:
    """Run sql, return (count, max_date_str). Returns (0, None) if table missing."""
    try:
        row = con.execute(sql, params).fetchone()
        if row:
            return (int(row[0] or 0), row[1])
        return (0, None)
    except Exception as exc:
        log.debug("diagnostic query skipped: %s", exc)
        return (0, None)


def _diagnose_one(
    con: sqlite3.Connection, strategy_id: str, since_iso: str
) -> dict[str, Any]:
    snap_count, snap_last = _safe_count_max(
        con,
        "SELECT COUNT(*), MAX(created_at) FROM analysis_snapshots"
        " WHERE strategy_id=? AND created_at>=?",
        (strategy_id, since_iso),
    )
    snap_all, _ = _safe_count_max(
        con,
        "SELECT COUNT(*), MAX(created_at) FROM analysis_snapshots WHERE strategy_id=?",
        (strategy_id,),
    )

    sig_count, sig_last = _safe_count_max(
        con,
        """SELECT COUNT(*), MAX(s.generated_at)
           FROM signals s
           JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
           WHERE a.strategy_id=? AND s.generated_at>=?""",
        (strategy_id, since_iso),
    )

    eval_count, eval_last = _safe_count_max(
        con,
        """SELECT COUNT(*), MAX(s.generated_at)
           FROM signal_evaluations e
           JOIN signals s ON s.run_id = e.signal_run_id AND s.symbol = e.symbol
           JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
           WHERE a.strategy_id=? AND s.generated_at>=?""",
        (strategy_id, since_iso),
    )

    pt_count, pt_last = _safe_count_max(
        con,
        """SELECT COUNT(*), MAX(s.generated_at)
           FROM paper_trades p
           JOIN signals s ON s.run_id = p.signal_run_id AND s.symbol = p.symbol
           JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
           WHERE a.strategy_id=? AND s.generated_at>=?""",
        (strategy_id, since_iso),
    )

    sr_count, sr_last = _safe_count_max(
        con,
        "SELECT COUNT(*), MAX(opened_at) FROM strategy_results"
        " WHERE strategy=? AND opened_at>=?",
        (strategy_id, since_iso),
    )

    if snap_all == 0:
        bottleneck = "A"
    elif sig_count == 0:
        bottleneck = "B"
    elif eval_count == 0:
        bottleneck = "C"
    elif pt_count == 0:
        bottleneck = "D"
    elif sr_count == 0:
        bottleneck = "E"
    else:
        bottleneck = "OK"

    last_seen = sr_last or pt_last or eval_last or sig_last or snap_last

    return {
        "strategy_id": strategy_id,
        "registered": snap_all > 0,
        "snapshots_30d": snap_count,
        "signals_30d": sig_count,
        "evaluations_30d": eval_count,
        "paper_trades_30d": pt_count,
        "strategy_results_30d": sr_count,
        "last_seen": last_seen,
        "bottleneck": bottleneck,
        "bottleneck_description": _BOTTLENECK_LABELS[bottleneck],
    }


def run_strategy_diagnostic(
    market_db: str = "data/market_data.db",
    strategy_ids: tuple[str, ...] | None = None,
    days: int = 30,
) -> list[dict[str, Any]]:
    since = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    targets = strategy_ids if strategy_ids is not None else _KNOWN_SIGNAL_STRATEGIES
    con = _conn(market_db)
    try:
        return [_diagnose_one(con, sid, since) for sid in targets]
    finally:
        con.close()
