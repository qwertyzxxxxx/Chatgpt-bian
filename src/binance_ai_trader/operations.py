from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from binance_ai_trader.hotlist.models import HotlistPerformanceStatistics
from binance_ai_trader.hotlist.performance import HotlistPerformanceTracker
from binance_ai_trader.hotlist.performance_repository import HotlistPerformanceRepository
from binance_ai_trader.hotlist.repository import HotlistWatchlistRepository
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap
from binance_ai_trader.strategy_lab.service import StrategyLab


BASELINE_V1_SHA256 = "a084040f829f5627cdf68d94d03dcaf56d9dc195bcdcef9abf4decc3cbd5b943"
PRIVATE_BINANCE_PATTERNS = (
    "/fapi/v1/order",
    "/fapi/v2/account",
    "/fapi/v2/balance",
    "/fapi/v2/position",
    "X-MBX-APIKEY",
)
LIVE_ORDER_PATTERNS = ("create_order(", "place_order(", "submit_order(")


class _NoNetworkClient:
    def klines(self, symbol: str, interval: str, limit: int = 200):
        raise RuntimeError("ops statistics never request network data")


def build_ops_status(database: Path, telegram_configured: bool) -> dict[str, object]:
    market = MarketDataRepository(database)
    watchlist = HotlistWatchlistRepository(database)
    performance = HotlistPerformanceRepository(database)
    try:
        statistics = HotlistPerformanceTracker(_NoNetworkClient(), performance).statistics()
        return {
            "database_health": market.sqlite_health(),
            "latest_regime": market.load_latest_regime_health(),
            "hotlist_watchlist_count": len(watchlist.all()),
            "hotlist_alerts_count": watchlist.alert_count(),
            "hotlist_performance_summary": _statistics_json(statistics),
            "runner_last_task_statuses": _latest_runner_statuses(database),
            "telegram_configured": telegram_configured,
        }
    finally:
        performance.close()
        watchlist.close()
        market.close()


def render_ops_daily(
    database: Path,
    baseline_config: Path,
    generated_at: datetime | None = None,
) -> str:
    now = (generated_at or datetime.now(UTC)).astimezone(UTC)
    watchlist = HotlistWatchlistRepository(database)
    performance = HotlistPerformanceRepository(database)
    market = MarketDataRepository(database)
    try:
        alerts_today = _alerts_today(database, now.date().isoformat())
        opportunities = performance.opportunities(limit=5)
        statistics = HotlistPerformanceTracker(_NoNetworkClient(), performance).statistics()
        try:
            standings = StrategyLab(
                market, SectorMap({}), baseline_config
            ).champion_league()
            champion = standings[0] if standings else None
        except ValueError:
            champion = None
    finally:
        market.close()
        performance.close()
        watchlist.close()

    lines = [
        "# Operational Daily Report",
        "",
        f"- **Generated at:** {now.isoformat(timespec='seconds')}",
        f"- **Hotlist alerts today:** {alerts_today}",
        "",
        "## Performance Statistics",
        "",
        f"- **Total tracked opportunities:** {statistics.total_opportunities}",
        f"- **Win rate:** {statistics.win_rate:.2f}%",
        f"- **TP1 rate:** {statistics.tp1_rate:.2f}%",
        f"- **TP2 rate:** {statistics.tp2_rate:.2f}%",
        f"- **Average RR:** {statistics.average_rr:.2f}",
        f"- **Average return:** {statistics.average_return:.2f}%",
        "",
        "## Current Champion Strategy",
        "",
    ]
    if champion is None:
        lines.append("No successful persisted backtest is available.")
    else:
        lines.extend(
            [
                f"- **Strategy:** `{champion.strategy_id}`",
                f"- **Score:** {champion.score:.6f}",
                f"- **Verdict:** {champion.verdict}",
            ]
        )
    lines.extend(
        [
            "",
            "## Top Opportunities",
            "",
            "| Symbol | Direction | Entry | SL | TP1 | TP2 | RR | Confidence | Expiry |",
            "| --- | :--- | ---: | ---: | ---: | ---: | ---: | :--- | --- |",
        ]
    )
    for item in opportunities:
        lines.append(
            f"| `{item.symbol}` | {item.direction} | {item.entry} | {item.stop_loss} | "
            f"{item.tp1} | {item.tp2} | {item.rr} | {item.confidence} | "
            f"{item.expires_at} |"
        )
    lines.extend(
        [
            "",
            "> **Research only.** This report is not trading advice and performs no live trades.",
            "",
        ]
    )
    return "\n".join(lines)


def run_safety_audit(
    repository_root: Path,
    baseline_config: Path,
) -> dict[str, object]:
    source_root = repository_root / "src" / "binance_ai_trader"
    files = tuple(
        path for path in source_root.rglob("*.py") if path.name != "operations.py"
    )
    findings = {
        "private_binance_endpoints": _find_patterns(files, PRIVATE_BINANCE_PATTERNS),
        "binance_api_key_strings": _find_patterns(files, ("BINANCE_API_KEY", "BINANCE_SECRET")),
        "live_trading_order_code": _find_patterns(files, LIVE_ORDER_PATTERNS),
    }
    baseline_digest = hashlib.sha256(baseline_config.read_bytes()).hexdigest()
    telegram_files = tuple(
        path for path in files if "telegram" in path.name or path.name == "cli.py"
    )
    hardcoded_telegram = _find_patterns(
        telegram_files, ("TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID=")
    )
    checks = {
        "no_binance_private_endpoints": not findings["private_binance_endpoints"],
        "no_binance_api_key_strings": not findings["binance_api_key_strings"],
        "no_live_trading_order_code": not findings["live_trading_order_code"],
        "baseline_v1_unchanged": baseline_digest == BASELINE_V1_SHA256,
        "telegram_secrets_only_from_env": not hardcoded_telegram,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "findings": {**findings, "hardcoded_telegram_secrets": hardcoded_telegram},
        "baseline_sha256": baseline_digest,
        "research_only": True,
    }


def _statistics_json(statistics: HotlistPerformanceStatistics) -> dict[str, object]:
    payload = asdict(statistics)
    return _decimal_strings(payload)


def _decimal_strings(value):
    if isinstance(value, dict):
        return {key: _decimal_strings(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_decimal_strings(item) for item in value]
    return str(value) if value.__class__.__name__ == "Decimal" else value


def _latest_runner_statuses(database: Path) -> list[dict[str, object]]:
    with sqlite3.connect(database) as connection:
        if not _table_exists(connection, "runner_events"):
            return []
        rows = connection.execute(
            """
            SELECT event_type, status, started_at, completed_at, error_message
            FROM runner_events AS current
            WHERE rowid = (
                SELECT rowid FROM runner_events
                WHERE event_type = current.event_type
                ORDER BY started_at DESC, rowid DESC LIMIT 1
            )
            ORDER BY event_type
            """
        ).fetchall()
    return [
        {
            "event_type": row[0],
            "status": row[1],
            "started_at": row[2],
            "completed_at": row[3],
            "error_message": row[4],
        }
        for row in rows
    ]


def _alerts_today(database: Path, day: str) -> int:
    with sqlite3.connect(database) as connection:
        if not _table_exists(connection, "hotlist_alerts"):
            return 0
        row = connection.execute(
            "SELECT COUNT(*) FROM hotlist_alerts WHERE substr(created_at, 1, 10)=?",
            (day,),
        ).fetchone()
    return int(row[0])


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _find_patterns(files: tuple[Path, ...], patterns: tuple[str, ...]) -> list[str]:
    findings = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if pattern in text:
                findings.append(f"{path.as_posix()}:{pattern}")
    return findings
