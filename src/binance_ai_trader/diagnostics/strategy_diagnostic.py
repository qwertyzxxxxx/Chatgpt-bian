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
    if _table_exists(con, "collection_runs") and _table_exists(con, "universe_snapshots"):
        row = con.execute(
            "SELECT id FROM collection_runs WHERE status='SUCCEEDED' AND started_at >= ?"
            " ORDER BY started_at DESC LIMIT 1",
            (since,),
        ).fetchone()
        if row:
            latest_run = row[0]
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

    # Status + dead reasons
    if stats.signals > 0 and stats.trades > 0:
        stats.status = _STATUS_ALIVE
    elif stats.signals > 0 and stats.trades == 0:
        stats.status = _STATUS_WEAK
        stats.dead_reasons = ["signals_not_traded"]
    elif stats.snapshots > 0 and stats.signals == 0:
        stats.status = _STATUS_DEAD
        if stats.scored > 0:
            regime_bias = meta.get("regime_bias")
            if regime_bias == "BEAR":
                regime = _latest_combined_regime(con)
                if regime and regime.upper() in ("BULL", "STRONG_BULL"):
                    stats.dead_reasons = ["disabled_by_regime"]
                else:
                    stats.dead_reasons = ["filters_too_strict"]
            else:
                stats.dead_reasons = ["filters_too_strict"]
        else:
            stats.dead_reasons = ["no_market_match"]
    else:
        stats.status = _STATUS_DEAD
        stats.dead_reasons = ["no_snapshots"]

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
    return {"ALIVE": "✅", "WEAK": "⚠️", "DEAD": "💀"}.get(status, "❓")


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
