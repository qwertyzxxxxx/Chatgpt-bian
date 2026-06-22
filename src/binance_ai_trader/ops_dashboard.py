from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_MAX_CHUNK = 4096


def _q(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    try:
        return con.execute(sql, params).fetchall()
    except Exception:
        return []


def _table(con: sqlite3.Connection, name: str) -> bool:
    r = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return r is not None


def _open_con(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def gather_6h_report(
    db_path: str,
    lw_db_path: str | None = None,
) -> str:
    now = datetime.now(UTC)
    window_start = (now - timedelta(hours=6)).isoformat(timespec="seconds")
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    con = _open_con(db_path)

    # ── Hotlist ──────────────────────────────────────────────
    scans_6h = 0
    if _table(con, "runner_events"):
        row = _q(con, "SELECT COUNT(*) AS n FROM runner_events WHERE event_type='hotlist_alert' AND started_at >= ?", (window_start,))
        scans_6h = int(row[0]["n"]) if row else 0

    candidates_6h = 0
    if _table(con, "hotlist_opportunities"):
        row = _q(con, "SELECT COUNT(*) AS n FROM hotlist_opportunities WHERE created_at >= ?", (window_start,))
        candidates_6h = int(row[0]["n"]) if row else 0

    alerts_6h = 0
    if _table(con, "hotlist_alerts"):
        row = _q(con, "SELECT COUNT(*) AS n FROM hotlist_alerts WHERE created_at >= ?", (window_start,))
        alerts_6h = int(row[0]["n"]) if row else 0

    settled_tp1 = settled_tp2 = settled_sl = 0
    hotlist_open = 0
    if _table(con, "strategy_results"):
        for res, col in [("TP1", "settled_tp1"), ("TP2", "settled_tp2"), ("SL", "settled_sl")]:
            row = _q(con,
                "SELECT COUNT(*) AS n FROM strategy_results WHERE strategy='hotlist' AND result=? AND closed_at >= ?",
                (res, window_start))
            val = int(row[0]["n"]) if row else 0
            if res == "TP1":
                settled_tp1 = val
            elif res == "TP2":
                settled_tp2 = val
            else:
                settled_sl = val
        row = _q(con,
            "SELECT COUNT(*) AS n FROM strategy_results WHERE strategy='hotlist' AND result='OPEN'")
        hotlist_open = int(row[0]["n"]) if row else 0

    settled_total = settled_tp1 + settled_tp2 + settled_sl

    # ── Gemini Committee ─────────────────────────────────────
    gc_total = gc_trade = gc_no_trade = 0
    top_reasons: list[tuple[str, int]] = []
    if _table(con, "gemini_committee_reviews"):
        row = _q(con,
            "SELECT COUNT(*) AS n FROM gemini_committee_reviews WHERE created_at >= ?",
            (window_start,))
        gc_total = int(row[0]["n"]) if row else 0
        row = _q(con,
            "SELECT COUNT(*) AS n FROM gemini_committee_reviews WHERE created_at >= ? AND decision='TRADE'",
            (window_start,))
        gc_trade = int(row[0]["n"]) if row else 0
        gc_no_trade = gc_total - gc_trade

        reasons_rows = _q(con,
            "SELECT reasons FROM gemini_committee_reviews WHERE created_at >= ? AND reasons IS NOT NULL AND reasons != '[]'",
            (window_start,))
        all_reasons: list[str] = []
        for r in reasons_rows:
            try:
                parsed = json.loads(r["reasons"])
                if isinstance(parsed, list):
                    all_reasons.extend(str(x) for x in parsed)
            except Exception:
                pass
        if all_reasons:
            top_reasons = Counter(all_reasons).most_common(5)

    # ── AI Macro ─────────────────────────────────────────────
    ai_macro_open = 0
    ai_macro_signals_6h = 0
    if _table(con, "strategy_results"):
        row = _q(con,
            "SELECT COUNT(*) AS n FROM strategy_results WHERE strategy='ai_macro' AND result='OPEN'")
        ai_macro_open = int(row[0]["n"]) if row else 0
        row = _q(con,
            "SELECT COUNT(*) AS n FROM strategy_results WHERE strategy='ai_macro' AND opened_at >= ?",
            (window_start,))
        ai_macro_signals_6h = int(row[0]["n"]) if row else 0

    con.close()

    # ── Leaderboard Watch ────────────────────────────────────
    lw_pool = lw_active = lw_reviews = lw_trade = lw_no_trade = 0
    if lw_db_path:
        try:
            lw_con = _open_con(lw_db_path)
            if _table(lw_con, "leaderboard_watch_items"):
                row = _q(lw_con, "SELECT COUNT(*) AS n FROM leaderboard_watch_items")
                lw_pool = int(row[0]["n"]) if row else 0
                row = _q(lw_con,
                    "SELECT COUNT(*) AS n FROM leaderboard_watch_items WHERE status IN ('NEW','ACTIVE')")
                lw_active = int(row[0]["n"]) if row else 0
            if _table(lw_con, "leaderboard_watch_reviews"):
                row = _q(lw_con,
                    "SELECT COUNT(*) AS n FROM leaderboard_watch_reviews WHERE created_at >= ?",
                    (window_start,))
                lw_reviews = int(row[0]["n"]) if row else 0
                row = _q(lw_con,
                    "SELECT COUNT(*) AS n FROM leaderboard_watch_reviews WHERE created_at >= ? AND decision='TRADE'",
                    (window_start,))
                lw_trade = int(row[0]["n"]) if row else 0
                lw_no_trade = lw_reviews - lw_trade
            lw_con.close()
        except Exception as exc:
            log.debug("leaderboard watch query failed: %s", exc)

    # ── Format ───────────────────────────────────────────────
    lines = [
        f"📊 系统运行日报（{now_str}）",
        "（过去6小时）",
        "",
        "Hotlist",
        f"扫描: {scans_6h}次",
        f"候选: {candidates_6h}",
        f"推送: {alerts_6h}",
        f"结算: {settled_total}",
        f"  胜: {settled_tp1 + settled_tp2} (TP1:{settled_tp1} TP2:{settled_tp2}) | 负: {settled_sl}",
        f"当前持仓: {hotlist_open}",
        "",
        "━━━━━━━━",
        "Gemini Committee",
        f"分析: {gc_total}次",
        f"TRADE: {gc_trade} | NO_TRADE: {gc_no_trade}",
    ]
    if top_reasons:
        lines.append("Top拒绝原因:")
        for reason, cnt in top_reasons:
            lines.append(f"  {reason}: {cnt}")
    elif gc_total == 0:
        lines.append("（本周期未运行或未启用）")
    else:
        lines.append("（暂无拒绝记录）")

    lines += [
        "",
        "━━━━━━━━",
        "AI Macro",
        f"推送: {ai_macro_signals_6h}",
        f"当前持仓: {ai_macro_open}",
        "" if ai_macro_signals_6h > 0 else "原因: 评分未达到阈值",
    ]

    lines += [
        "",
        "━━━━━━━━",
        "Leaderboard Watch",
        f"观察池: {lw_pool}",
        f"ACTIVE: {lw_active}",
        f"Gemini分析: {lw_reviews}",
        f"TRADE: {lw_trade} | NO_TRADE: {lw_no_trade}",
        "",
        "仅供研究 | 不进行实盘交易",
    ]

    return "\n".join(lines)


def gather_hourly_settlement(db_path: str) -> str | None:
    """
    Returns formatted hourly settlement summary, or None if nothing settled.
    """
    now = datetime.now(UTC)
    window_start = (now - timedelta(hours=1)).isoformat(timespec="seconds")

    con = _open_con(db_path)
    if not _table(con, "strategy_results"):
        con.close()
        return None

    rows = _q(con,
        """
        SELECT strategy, symbol, direction, result, pnl_pct, rr_realized
        FROM strategy_results
        WHERE closed_at >= ? AND result != 'OPEN'
        ORDER BY closed_at DESC
        """,
        (window_start,))

    hotlist_open_row = _q(con,
        "SELECT COUNT(*) AS n FROM strategy_results WHERE strategy='hotlist' AND result='OPEN'")
    hotlist_open = int(hotlist_open_row[0]["n"]) if hotlist_open_row else 0

    con.close()

    if not rows:
        return None

    tp1 = sum(1 for r in rows if r["result"] == "TP1")
    tp2 = sum(1 for r in rows if r["result"] == "TP2")
    sl = sum(1 for r in rows if r["result"] == "SL")

    pnl_records = [
        (r["symbol"], float(r["pnl_pct"]))
        for r in rows
        if r["pnl_pct"] is not None
    ]
    pnl_records.sort(key=lambda x: x[1], reverse=True)

    winners = [(s, p) for s, p in pnl_records if p > 0][:3]
    losers = [(s, p) for s, p in pnl_records if p < 0]

    now_str = now.strftime("%H:%M UTC")
    lines = [
        f"📈 本小时结算（截至 {now_str}）",
        "",
        "Hotlist",
        f"TP1: {tp1}  TP2: {tp2}  SL: {sl}",
    ]

    if winners:
        lines.append("收益前三：")
        for sym, pnl in winners:
            sign = "+" if pnl >= 0 else ""
            lines.append(f"  {sym} {sign}{pnl:.2f}%")

    if losers:
        lines.append("亏损：")
        for sym, pnl in losers:
            lines.append(f"  {sym} {pnl:.2f}%")

    lines += [
        f"当前持仓: {hotlist_open}",
        "",
        "仅供研究 | 不进行实盘交易",
    ]

    return "\n".join(lines)


def send_telegram(text: str, bot_token: str, chat_id: str, timeout: int = 10) -> bool:
    chunks = [text[i:i + _MAX_CHUNK] for i in range(0, len(text), _MAX_CHUNK)]
    ok = True
    for chunk in chunks:
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
            log.warning("Telegram send failed: %s", exc)
            ok = False
    return ok
