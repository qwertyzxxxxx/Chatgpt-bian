"""V3 Push Queue — all Telegram pushes go through here.

Every candidate that passes Risk + Dedup is enqueued here.
The sender picks up QUEUED entries, sends them, and marks SENT / FAILED.
Retry logic is handled by the sender (retry_count + cooldown_until).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

_DDL = """
CREATE TABLE IF NOT EXISTS v3_push_queue (
    push_id              TEXT PRIMARY KEY,
    signal_id            TEXT NOT NULL,
    strategy_id          TEXT NOT NULL,
    telegram_message_id  TEXT,
    pushed_at            TEXT,
    push_status          TEXT NOT NULL DEFAULT 'QUEUED',
    retry_count          INTEGER NOT NULL DEFAULT 0,
    cooldown_until       TEXT,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v3_push_signal
    ON v3_push_queue(signal_id);
CREATE INDEX IF NOT EXISTS idx_v3_push_status
    ON v3_push_queue(push_status, cooldown_until);
"""

_STATUSES = {"QUEUED", "SENT", "FAILED", "SKIPPED"}


@dataclass(frozen=True, slots=True)
class V3PushItem:
    push_id: str
    signal_id: str
    strategy_id: str
    telegram_message_id: str | None
    pushed_at: str | None
    push_status: str
    retry_count: int
    cooldown_until: str | None
    created_at: str


class V3PushQueueRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.executescript(_DDL)

    def enqueue(self, signal_id: str, strategy_id: str) -> V3PushItem:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        item = V3PushItem(
            push_id=str(uuid4()),
            signal_id=signal_id,
            strategy_id=strategy_id,
            telegram_message_id=None,
            pushed_at=None,
            push_status="QUEUED",
            retry_count=0,
            cooldown_until=None,
            created_at=now,
        )
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """INSERT INTO v3_push_queue
                   (push_id, signal_id, strategy_id, push_status, retry_count, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (item.push_id, item.signal_id, item.strategy_id,
                 item.push_status, item.retry_count, item.created_at),
            )
        return item

    def mark_sent(self, push_id: str, telegram_message_id: str | None = None) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """UPDATE v3_push_queue
                   SET push_status='SENT', pushed_at=?, telegram_message_id=?
                   WHERE push_id=?""",
                (now, telegram_message_id, push_id),
            )

    def mark_failed(self, push_id: str, cooldown_seconds: int = 300) -> None:
        cooldown = (datetime.now(UTC) + timedelta(seconds=cooldown_seconds)).isoformat(
            timespec="seconds"
        )
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """UPDATE v3_push_queue
                   SET push_status='FAILED',
                       retry_count = retry_count + 1,
                       cooldown_until = ?
                   WHERE push_id=?""",
                (cooldown, push_id),
            )

    def load_pending(self) -> list[V3PushItem]:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM v3_push_queue
                   WHERE push_status IN ('QUEUED','FAILED')
                     AND (cooldown_until IS NULL OR cooldown_until <= ?)
                   ORDER BY created_at""",
                (now,),
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    def load_by_signal(self, signal_id: str) -> list[V3PushItem]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM v3_push_queue WHERE signal_id=? ORDER BY created_at",
                (signal_id,),
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    def already_queued(self, signal_id: str) -> bool:
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                "SELECT 1 FROM v3_push_queue WHERE signal_id=? LIMIT 1", (signal_id,)
            ).fetchone()
        return row is not None


def _row_to_item(row: sqlite3.Row) -> V3PushItem:
    return V3PushItem(
        push_id=row["push_id"],
        signal_id=row["signal_id"],
        strategy_id=row["strategy_id"],
        telegram_message_id=row["telegram_message_id"],
        pushed_at=row["pushed_at"],
        push_status=row["push_status"],
        retry_count=row["retry_count"],
        cooldown_until=row["cooldown_until"],
        created_at=row["created_at"],
    )
