"""Hourly strategy self-report: queries 24-hour stats from all modules and sends to Telegram."""
from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

_STRATEGY_ENTRIES = [
    ("baseline_v1",              "📈 Baseline V1"),
    ("breakout_hunter_v1",       "🎯 Breakout Hunter V1"),
    ("bear_short_space80_v1",    "📉 Bear Short Space80"),
    ("capital_60_80_space80_v1", "💰 Capital 60-80 Space80"),
    ("range_disabled_v1",        "⛔ Range Disabled V1"),
]


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


def _query_hotlist_stats(con: sqlite3.Connection, since: str) -> dict[str, int]:
    alerts = 0
    if _table_exists(con, "hotlist_alerts"):
        alerts = _safe_count(
            con,
            "SELECT COUNT(*) FROM hotlist_alerts WHERE created_at >= ?",
            (since,),
        )

    open_, tp1, tp2, sl = 0, 0, 0, 0
    if _table_exists(con, "hotlist_outcomes"):
        rows = con.execute(
            "SELECT UPPER(status), COUNT(*) FROM hotlist_outcomes"
            " WHERE evaluated_at >= ? GROUP BY status",
            (since,),
        ).fetchall()
        outcome_map: dict[str, int] = {str(s): int(n) for s, n in rows}
        open_ = outcome_map.get("OPEN", 0)
        tp1 = outcome_map.get("TP1_HIT", 0)
        tp2 = max(
            outcome_map.get("WIN", 0),
            outcome_map.get("TP2_HIT", 0),
            outcome_map.get("WIN_TP2", 0),
        )
        sl = max(outcome_map.get("LOSS", 0), outcome_map.get("SL_HIT", 0))

    return {"alerts": alerts, "open": open_, "tp1": tp1, "tp2": tp2, "sl": sl}


def _query_signals_by_strategy(con: sqlite3.Connection, since: str) -> dict[str, int]:
    if not (_table_exists(con, "signals") and _table_exists(con, "analysis_snapshots")):
        return {}
    rows = con.execute(
        """
        SELECT COALESCE(a.strategy_id, 'baseline_v1') AS sid, COUNT(*) AS n
        FROM signals s
        LEFT JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
        WHERE s.generated_at >= ?
        GROUP BY sid
        """,
        (since,),
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _query_trades_by_strategy(con: sqlite3.Connection, since: str) -> dict[str, int]:
    if not (
        _table_exists(con, "paper_trades")
        and _table_exists(con, "signals")
        and _table_exists(con, "analysis_snapshots")
    ):
        return {}
    rows = con.execute(
        """
        SELECT COALESCE(a.strategy_id, 'baseline_v1') AS sid, COUNT(*) AS n
        FROM paper_trades pt
        JOIN signals s ON s.run_id = pt.signal_run_id AND s.symbol = pt.symbol
        LEFT JOIN analysis_snapshots a ON a.snapshot_id = s.snapshot_id
        WHERE pt.processed_at >= ?
        GROUP BY sid
        """,
        (since,),
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _query_ai_macro_stats(ai_macro_db: str | None, since: str) -> dict[str, int] | None:
    if not ai_macro_db:
        return None
    try:
        con = sqlite3.connect(ai_macro_db)
        con.row_factory = sqlite3.Row
        if not _table_exists(con, "ai_macro_trades"):
            con.close()
            return None
        candidates = _safe_count(
            con,
            "SELECT COUNT(*) FROM ai_macro_trades WHERE created_at >= ?",
            (since,),
        )
        trades = _safe_count(
            con,
            "SELECT COUNT(*) FROM ai_macro_trades WHERE created_at >= ?"
            " AND status NOT IN ('SKIPPED','PENDING')",
            (since,),
        )
        con.close()
        return {"candidates": candidates, "trades": trades}
    except Exception as exc:
        log.debug("ai_macro stats failed: %s", exc)
        return None


def _query_gemini_stats(con: sqlite3.Connection, since: str) -> dict[str, object]:
    result: dict[str, object] = {
        "reviews": 0,
        "TRADE": 0,
        "NO_TRADE": 0,
        "SKIPPED": 0,
        "top_reasons": [],
    }
    if not _table_exists(con, "gemini_committee_reviews"):
        return result

    total = _safe_count(
        con,
        "SELECT COUNT(*) FROM gemini_committee_reviews WHERE created_at >= ?",
        (since,),
    )
    result["reviews"] = total
    if total == 0:
        return result

    rows = con.execute(
        "SELECT UPPER(decision), COUNT(*) FROM gemini_committee_reviews"
        " WHERE created_at >= ? GROUP BY UPPER(decision)",
        (since,),
    ).fetchall()
    for decision, cnt in rows:
        key = str(decision)
        if key in ("TRADE", "NO_TRADE", "SKIPPED"):
            result[key] = int(cnt)

    skipped_by_status = _safe_count(
        con,
        "SELECT COUNT(*) FROM gemini_committee_reviews"
        " WHERE created_at >= ? AND UPPER(status) = 'SKIPPED'",
        (since,),
    )
    result["SKIPPED"] = max(int(result.get("SKIPPED", 0)), skipped_by_status)

    reason_rows = con.execute(
        """
        SELECT risk_level, COUNT(*) AS n
        FROM gemini_committee_reviews
        WHERE created_at >= ? AND UPPER(decision) = 'NO_TRADE' AND risk_level IS NOT NULL
        GROUP BY risk_level ORDER BY n DESC LIMIT 3
        """,
        (since,),
    ).fetchall()
    result["top_reasons"] = [f"{row[0]}×{row[1]}" for row in reason_rows]
    return result


def _query_leaderboard_stats(lw_db: str | None, since: str) -> dict[str, int]:
    result: dict[str, int] = {"pool_size": 0, "reviews": 0, "TRADE": 0, "NO_TRADE": 0}
    if not lw_db:
        return result
    try:
        con = sqlite3.connect(lw_db)
        if _table_exists(con, "leaderboard_watch_items"):
            result["pool_size"] = _safe_count(
                con,
                "SELECT COUNT(*) FROM leaderboard_watch_items"
                " WHERE status IN ('ACTIVE', 'NEW', 'OPEN')",
            )
        if _table_exists(con, "leaderboard_watch_reviews"):
            result["reviews"] = _safe_count(
                con,
                "SELECT COUNT(*) FROM leaderboard_watch_reviews WHERE created_at >= ?",
                (since,),
            )
            for decision in ("TRADE", "NO_TRADE"):
                result[decision] = _safe_count(
                    con,
                    "SELECT COUNT(*) FROM leaderboard_watch_reviews"
                    " WHERE created_at >= ? AND UPPER(decision) = ?",
                    (since, decision),
                )
        con.close()
    except Exception as exc:
        log.debug("leaderboard stats failed: %s", exc)
    return result


def build_hourly_report(
    market_db: str,
    ai_macro_db: str | None = None,
    lw_db: str | None = None,
) -> str:
    now = datetime.now(UTC)
    since = (now - timedelta(hours=24)).isoformat(timespec="seconds")

    try:
        con = sqlite3.connect(market_db)
    except Exception as exc:
        return f"⚠️ 策略自检：无法连接数据库 — {exc}"

    try:
        hotlist = _query_hotlist_stats(con, since)
        signals_by_strat = _query_signals_by_strategy(con, since)
        trades_by_strat = _query_trades_by_strategy(con, since)
        gemini = _query_gemini_stats(con, since)
    finally:
        con.close()

    ai_macro = _query_ai_macro_stats(ai_macro_db, since)
    lw = _query_leaderboard_stats(lw_db, since)

    alerts: list[str] = []

    lines: list[str] = [
        "📊 策略自检报告",
        f"{now.strftime('%Y-%m-%d %H:%M UTC')}（过去24小时）",
        "",
        "🔔 Hotlist",
        f"  • alerts: {hotlist['alerts']}",
        f"  • open: {hotlist['open']} | TP1: {hotlist['tp1']}"
        f" | TP2: {hotlist['tp2']} | SL: {hotlist['sl']}",
    ]

    for strategy_id, label in _STRATEGY_ENTRIES:
        sig = signals_by_strat.get(strategy_id, 0)
        trade = trades_by_strat.get(strategy_id, 0)
        dead = sig == 0 and trade == 0
        flag = "  ⚠️ DEAD" if dead else ""
        lines += ["", label, f"  • signals: {sig} | trades: {trade}{flag}"]
        if dead:
            alerts.append(f"⚠️ {label}：24小时无信号和交易 → DEAD")

    lines += ["", "🤖 AI Macro"]
    if ai_macro is not None:
        lines.append(
            f"  • candidates: {ai_macro['candidates']} | trades: {ai_macro['trades']}"
        )
    else:
        lines.append("  • 未启用或无数据")

    g_reviews = int(gemini["reviews"])
    g_trade = int(gemini["TRADE"])
    g_no_trade = int(gemini["NO_TRADE"])
    g_skipped = int(gemini["SKIPPED"])
    top_reasons: list[str] = list(gemini.get("top_reasons", []))  # type: ignore[arg-type]

    lines += [
        "",
        "🧠 Gemini Committee",
        f"  • reviews: {g_reviews} | TRADE: {g_trade}"
        f" | NO_TRADE: {g_no_trade} | SKIPPED: {g_skipped}",
    ]
    if top_reasons:
        lines.append(f"  • 主要拒绝原因: {', '.join(top_reasons)}")
    if g_reviews > 0 and g_trade == 0:
        lines.append("  ⚠️ Gemini无有效交易")
        alerts.append(
            f"⚠️ Gemini：已审查{g_reviews}次，0次TRADE → 请检查候选质量或阈值"
        )

    lines += [
        "",
        "👀 Leaderboard Watch",
        f"  • pool: {lw['pool_size']} | reviews: {lw['reviews']}"
        f" | TRADE: {lw['TRADE']} | NO_TRADE: {lw['NO_TRADE']}",
    ]

    if alerts:
        lines += ["", "━━━━━━━━━━━━", *alerts]

    lines += ["", "仅供研究 | 不进行实盘交易"]
    return "\n".join(lines)


def send_hourly_report(
    market_db: str,
    bot_token: str,
    chat_id: str,
    ai_macro_db: str | None = None,
    lw_db: str | None = None,
    timeout: int = 10,
) -> bool:
    text = build_hourly_report(market_db, ai_macro_db=ai_macro_db, lw_db=lw_db)
    _MAX = 4096
    ok = True
    for chunk in [text[i: i + _MAX] for i in range(0, len(text), _MAX)]:
        payload = json.dumps({"chat_id": chat_id, "text": chunk}).encode()
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout):
                pass
        except Exception as exc:
            log.warning("Hourly report send failed: %s", exc)
            ok = False
    return ok
