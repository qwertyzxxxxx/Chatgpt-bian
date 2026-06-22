from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository

log = logging.getLogger(__name__)

_MONITORED_TASKS: dict[str, tuple[str, timedelta]] = {
    "scan":               ("信号扫描",         timedelta(minutes=20)),
    "hotlist_alert":      ("热门点位推送",      timedelta(minutes=20)),
    "hotlist_performance":("热门绩效追踪",      timedelta(minutes=20)),
    "gemini_committee":   ("Gemini AI委员会",   timedelta(hours=5)),
    "performance_settle": ("绩效结算",          timedelta(hours=2)),
    "performance_summary":("绩效统计推送",      timedelta(hours=7)),
    "leaderboard_update": ("排行榜更新",        timedelta(minutes=20)),
    "leaderboard_gemini": ("排行榜Gemini",      timedelta(hours=5)),
}


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _check_hotlist_stall(db_path: str, now: datetime) -> str | None:
    """Return a warning line if hotlist scanned normally but produced zero candidates for 60+ min."""
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cutoff = (now - timedelta(minutes=60)).isoformat(timespec="seconds")
        if not _table_exists(con, "hotlist_opportunities"):
            con.close()
            return None
        row = con.execute(
            "SELECT COUNT(*) AS n FROM hotlist_opportunities WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()
        recent_candidates = int(row["n"])
        con.close()
        if recent_candidates == 0:
            return "⚠️ Hotlist：过去60分钟扫描正常但无候选——请检查市场条件或阈值"
    except Exception as exc:
        log.debug("hotlist stall check failed: %s", exc)
    return None


def _check_gemini_trade_drought(db_path: str, now: datetime) -> str | None:
    """Return a warning line if gemini ran in last 24h but produced 0 TRADE decisions."""
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cutoff = (now - timedelta(hours=24)).isoformat(timespec="seconds")
        if not _table_exists(con, "gemini_committee_reviews"):
            con.close()
            return None
        total_row = con.execute(
            "SELECT COUNT(*) AS n FROM gemini_committee_reviews WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()
        total = int(total_row["n"])
        if total == 0:
            con.close()
            return None
        trade_row = con.execute(
            "SELECT COUNT(*) AS n FROM gemini_committee_reviews WHERE created_at >= ? AND decision='TRADE'",
            (cutoff,),
        ).fetchone()
        trade_count = int(trade_row["n"])
        con.close()
        if trade_count == 0:
            return f"⚠️ Gemini：过去24小时分析{total}次，0次TRADE——请检查阈值或候选池质量"
    except Exception as exc:
        log.debug("gemini drought check failed: %s", exc)
    return None


def check_strategy_health(
    repository: "MarketDataRepository",
    db_path: str | None = None,
) -> str:
    now = datetime.now(UTC)
    lines = [
        "🔧 策略运行状态报告",
        f"检查时间：{now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    any_issue = False
    for task_id, (label, max_age) in _MONITORED_TASKS.items():
        summary = repository.load_runner_task_summary(task_id)

        if summary is None:
            lines.append(f"⏸️ {label}：从未运行（未启用或尚未开始）")
            any_issue = True
            continue

        status = summary["status"]
        started_at_str = summary["started_at"]
        error_msg = summary.get("error_message") or ""

        try:
            last_dt = _parse_dt(started_at_str)
            age = now - last_dt
            time_str = last_dt.strftime("%H:%M UTC")
            age_min = int(age.total_seconds() / 60)
        except Exception:
            lines.append(f"⚠️ {label}：时间解析失败")
            any_issue = True
            continue

        if status == "FAILED":
            short_err = error_msg[:60] + "…" if len(error_msg) > 60 else error_msg
            lines.append(f"❌ {label}：上次失败（{time_str}）{' — ' + short_err if short_err else ''}")
            any_issue = True
        elif status == "RUNNING":
            lines.append(f"🔄 {label}：运行中（始于 {time_str}）")
        elif age > max_age:
            lines.append(f"⚠️ {label}：滞后 {age_min}分钟（上次 {time_str}）")
            any_issue = True
        else:
            lines.append(f"✅ {label}：正常（上次 {time_str}）")

    if db_path:
        lines.append("")
        stall_warn = _check_hotlist_stall(db_path, now)
        if stall_warn:
            lines.append(stall_warn)
            any_issue = True
        drought_warn = _check_gemini_trade_drought(db_path, now)
        if drought_warn:
            lines.append(drought_warn)
            any_issue = True

    lines.append("")
    if any_issue:
        lines.append("⚠️ 存在异常策略，请检查运行日志。")
    else:
        lines.append("✅ 所有策略运行正常。")

    lines.append("\n仅供研究 | 不进行实盘交易")
    return "\n".join(lines)


def send_strategy_health(
    repository: "MarketDataRepository",
    bot_token: str,
    chat_id: str,
    timeout: int = 10,
    db_path: str | None = None,
) -> bool:
    text = check_strategy_health(repository, db_path=db_path)
    _MAX = 4096
    chunks = [text[i : i + _MAX] for i in range(0, len(text), _MAX)]
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
