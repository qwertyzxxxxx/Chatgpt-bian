"""V3 Push Queue — PostgreSQL backend.

Every candidate that passes Risk + Dedup is enqueued here.
The sender picks up QUEUED entries, sends them, and marks SENT / FAILED.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from binance_ai_trader.v3.storage.pg import get_conn


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
    """All operations go to PostgreSQL."""

    def __init__(self, db_path=None) -> None:
        pass

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
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO v3_push_queue
                       (push_id, signal_id, strategy_id, push_status, retry_count, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (push_id) DO NOTHING""",
                    (item.push_id, item.signal_id, item.strategy_id,
                     item.push_status, item.retry_count, item.created_at),
                )
            conn.commit()
        finally:
            conn.close()
        return item

    def mark_sent(self, push_id: str, telegram_message_id: str | None = None) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE v3_push_queue
                       SET push_status='SENT', pushed_at=%s, telegram_message_id=%s
                       WHERE push_id=%s""",
                    (now, telegram_message_id, push_id),
                )
            conn.commit()
        finally:
            conn.close()

    def mark_failed(self, push_id: str, cooldown_seconds: int = 300) -> None:
        cooldown = (datetime.now(UTC) + timedelta(seconds=cooldown_seconds)).isoformat(
            timespec="seconds"
        )
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE v3_push_queue
                       SET push_status='FAILED',
                           retry_count = retry_count + 1,
                           cooldown_until = %s
                       WHERE push_id=%s""",
                    (cooldown, push_id),
                )
            conn.commit()
        finally:
            conn.close()

    def load_pending(self) -> list[V3PushItem]:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT push_id, signal_id, strategy_id, telegram_message_id,
                              pushed_at, push_status, retry_count, cooldown_until, created_at
                       FROM v3_push_queue
                       WHERE push_status IN ('QUEUED','FAILED')
                         AND (cooldown_until IS NULL OR cooldown_until <= %s)
                       ORDER BY created_at""",
                    (now,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_item(r) for r in rows]

    def load_by_signal(self, signal_id: str) -> list[V3PushItem]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT push_id, signal_id, strategy_id, telegram_message_id,
                              pushed_at, push_status, retry_count, cooldown_until, created_at
                       FROM v3_push_queue WHERE signal_id=%s ORDER BY created_at""",
                    (signal_id,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [_row_to_item(r) for r in rows]

    def already_queued(self, signal_id: str) -> bool:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM v3_push_queue WHERE signal_id=%s LIMIT 1", (signal_id,)
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return row is not None


def _row_to_item(row: tuple) -> V3PushItem:
    return V3PushItem(
        push_id=row[0],
        signal_id=row[1],
        strategy_id=row[2],
        telegram_message_id=row[3],
        pushed_at=row[4],
        push_status=row[5],
        retry_count=row[6],
        cooldown_until=row[7],
        created_at=row[8],
    )
