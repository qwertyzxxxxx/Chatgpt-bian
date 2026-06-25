"""V2 Immediate Alert — [V2] ALERT

Triggers (sent immediately, not waiting for next health check):
  - Runner crash
  - Binance API consecutive failures >= threshold
  - Settlement consecutive failures >= threshold
  - DB write failure
  - No scan for > 2 hours (checked in health check only if enabled)
"""
from __future__ import annotations

import logging

from binance_ai_trader.notifications import TelegramNotifier

log = logging.getLogger(__name__)

_API_FAIL_THRESHOLD = 3
_SETTLE_FAIL_THRESHOLD = 3


class V2AlertSender:
    def __init__(self, notifier: TelegramNotifier) -> None:
        self._notifier = notifier

    def send(self, reason: str, detail: str = "") -> None:
        body = detail.strip()
        msg = f"[V2] ALERT\n\n{reason}"
        if body:
            msg += f"\n{body}"
        try:
            self._notifier.send(msg)
            log.warning("[V2] ALERT sent: %s", reason)
        except Exception as exc:
            log.error("[V2] failed to send alert: %s | original: %s", exc, reason)

    def maybe_alert_api(self, consecutive: int) -> bool:
        if consecutive >= _API_FAIL_THRESHOLD:
            self.send(
                f"Binance API 连续失败 {consecutive} 次",
                "请检查网络连接和 Binance API 状态。",
            )
            return True
        return False

    def maybe_alert_settle(self, consecutive: int) -> bool:
        if consecutive >= _SETTLE_FAIL_THRESHOLD:
            self.send(
                f"Settlement 连续失败 {consecutive} 次",
                "请检查 Binance kline API 和 DB 写入。",
            )
            return True
        return False

    def alert_db_failure(self, detail: str = "") -> None:
        self.send("DB 写入失败", detail)

    def alert_scan_overdue(self, hours: float) -> None:
        self.send(
            f"超过 {hours:.1f} 小时未扫描",
            "V2 hotlist scan 任务可能已停止运行。",
        )

    def alert_runner_crash(self, task: str, detail: str = "") -> None:
        self.send(f"Runner 任务崩溃: {task}", detail)
