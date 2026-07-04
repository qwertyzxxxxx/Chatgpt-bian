"""V3 Startup report — sent once on runner boot.

Format:
  [V3] 🚀 Started
  Version   v3.0.0-phase2
  Strategy  hotlist_momentum_v3
  DB        /data/market_data.db
  PID       12345
  Started   2026-07-04 10:00:00 UTC
  Scan      every 15min
  Settle    every 15min
  Report    every 1h
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

from binance_ai_trader.notifications import TelegramNotifier

_VERSION = "v3.0.0-phase2"


def send_v3_startup(
    notifier: TelegramNotifier,
    strategy_id: str,
    db_path: str,
    scan_interval_minutes: int = 15,
    settle_interval_minutes: int = 15,
    report_interval_hours: int = 1,
    health_interval_hours: int = 6,
    shadow_report_enabled: bool = True,
    health_check_enabled: bool = True,
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    pid = os.getpid()

    lines = [
        f"[V3] 🚀 Started",
        f"Version    {_VERSION}",
        f"Strategy   {strategy_id}",
        f"DB         {db_path}",
        f"PID        {pid}",
        f"Started    {now}",
        f"Scan       每 {scan_interval_minutes}min",
        f"Settle     每 {settle_interval_minutes}min",
        f"Report     每 {report_interval_hours}h" if shadow_report_enabled else "Report     disabled",
        f"Health     每 {health_interval_hours}h" if health_check_enabled else "Health     disabled",
    ]
    notifier.send("\n".join(lines))
