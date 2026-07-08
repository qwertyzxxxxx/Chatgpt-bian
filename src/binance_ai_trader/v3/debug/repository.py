"""Scan diagnostic snapshot — powers the /v4debug Telegram command.

Persisted to PostgreSQL (not in-memory) so the command server, which may run
in a different thread/restart cycle than the scan task, can always read the
latest ranking diagnostic regardless of process boundaries.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from binance_ai_trader.v3.storage.pg import get_conn

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScanDebugSnapshot:
    strategy_id: str
    created_at: str
    pool_size: int
    computed_count: int
    live_eligible_count: int
    top10: list[dict]
    crowded_out: list[dict]


class ScanDebugRepository:
    def save(self, snapshot: ScanDebugSnapshot) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO v3_scan_debug (
                        strategy_id, created_at, pool_size, computed_count,
                        live_eligible_count, top10_json, crowded_out_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (strategy_id) DO UPDATE SET
                        created_at           = EXCLUDED.created_at,
                        pool_size            = EXCLUDED.pool_size,
                        computed_count       = EXCLUDED.computed_count,
                        live_eligible_count  = EXCLUDED.live_eligible_count,
                        top10_json           = EXCLUDED.top10_json,
                        crowded_out_json     = EXCLUDED.crowded_out_json
                    """,
                    (
                        snapshot.strategy_id, snapshot.created_at, snapshot.pool_size,
                        snapshot.computed_count, snapshot.live_eligible_count,
                        json.dumps(snapshot.top10), json.dumps(snapshot.crowded_out),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def load(self, strategy_id: str) -> ScanDebugSnapshot | None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT strategy_id, created_at, pool_size, computed_count, "
                    "live_eligible_count, top10_json, crowded_out_json "
                    "FROM v3_scan_debug WHERE strategy_id=%s",
                    (strategy_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return ScanDebugSnapshot(
                    strategy_id=row[0],
                    created_at=row[1],
                    pool_size=row[2],
                    computed_count=row[3],
                    live_eligible_count=row[4],
                    top10=json.loads(row[5]),
                    crowded_out=json.loads(row[6]),
                )
        finally:
            conn.close()
