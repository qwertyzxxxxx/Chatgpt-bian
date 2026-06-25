"""V2 Health Check — [V2] Health Check  (every 6h, default ON, can be disabled)

Checks:
  Scan:        last_scan_at within 30min
  Settle:      last_settle_at within 30min
  Report:      last_report_at within 70min
  Binance API: api_consecutive_failures == 0
  DB:          db_failure == False
  Errors last 6h: count

If all OK → short summary.
If any FAIL → show error summary.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v2.monitoring.health_tracker import V2HealthTracker

log = logging.getLogger(__name__)

_OK = "OK"
_FAIL = "FAIL"


def _check_recency(ts: datetime | None, max_minutes: int) -> str:
    if ts is None:
        return _FAIL
    if (datetime.now(UTC) - ts) > timedelta(minutes=max_minutes):
        return _FAIL
    return _OK


class V2HealthReporter:
    def __init__(
        self,
        notifier: TelegramNotifier,
        tracker: V2HealthTracker,
    ) -> None:
        self._notifier = notifier
        self._tracker = tracker

    def send_health_check(self) -> None:
        try:
            msg = self._build_message()
            self._notifier.send(msg)
            log.info("[V2] health check sent")
        except Exception as exc:
            log.warning("[V2] failed to send health check: %s", exc)

    def _build_message(self) -> str:
        t = self._tracker
        scan_st   = _check_recency(t.last_scan_at,   max_minutes=30)
        settle_st = _check_recency(t.last_settle_at, max_minutes=30)
        report_st = _check_recency(t.last_report_at, max_minutes=70)
        api_st    = _OK if t.api_consecutive_failures == 0 else _FAIL
        db_st     = _OK if not t.db_failure else _FAIL

        recent_errors = t.errors_last_n_hours(6)
        error_count = len(recent_errors)

        statuses = [scan_st, settle_st, report_st, api_st, db_st]
        all_ok = all(s == _OK for s in statuses) and error_count == 0

        lines = ["[V2] Health Check\n"]
        lines.append(f"Scan:        {scan_st}")
        lines.append(f"Settle:      {settle_st}")
        lines.append(f"Summary:     {report_st}")
        lines.append(f"Binance API: {api_st}")
        lines.append(f"DB:          {db_st}")
        lines.append(f"Errors last 6h: {error_count}")

        if all_ok:
            lines.append("\n✅ All OK")
        else:
            lines.append("\n⚠ Issues detected:")
            if scan_st == _FAIL:
                age = _age_str(t.last_scan_at)
                lines.append(f"  • Scan last ran {age}")
            if settle_st == _FAIL:
                age = _age_str(t.last_settle_at)
                lines.append(f"  • Settle last ran {age}")
            if report_st == _FAIL:
                age = _age_str(t.last_report_at)
                lines.append(f"  • Report last sent {age}")
            if api_st == _FAIL:
                lines.append(f"  • API consecutive failures: {t.api_consecutive_failures}")
            if db_st == _FAIL:
                lines.append("  • DB write failure recorded")
            if error_count > 0:
                lines.append(f"  • Recent errors:")
                for ts, task, msg in recent_errors[-5:]:
                    lines.append(f"    [{ts.strftime('%H:%M')}] {task}: {msg[:60]}")

        return "\n".join(lines)


def _age_str(ts: datetime | None) -> str:
    if ts is None:
        return "never"
    delta = datetime.now(UTC) - ts
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m = rem // 60
    return f"{h}h{m:02d}m ago" if h else f"{m}m ago"
