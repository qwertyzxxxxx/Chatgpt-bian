"""V2 Strategy Registry — single source of truth for enabled strategies."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class V2Strategy:
    strategy_id: str
    name: str
    enabled: bool
    status: str
    parameters_json: str
    max_hold_hours: int
    created_at: str
    updated_at: str

    @property
    def parameters(self) -> dict:
        try:
            return json.loads(self.parameters_json)
        except Exception:
            return {}

    @property
    def max_open_orders(self) -> int:
        return int(self.parameters.get("max_open_orders", 3))

    @property
    def blacklist(self) -> frozenset[str]:
        return frozenset(self.parameters.get("blacklist", []))


_DDL = """
CREATE TABLE IF NOT EXISTS v2_strategies (
    strategy_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'PAPER',
    parameters_json TEXT NOT NULL DEFAULT '{}',
    max_hold_hours  INTEGER NOT NULL DEFAULT 24,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


class V2StrategyRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_table(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)

    def upsert(self, strategy: V2Strategy) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_strategies
                    (strategy_id, name, enabled, status, parameters_json,
                     max_hold_hours, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    name            = excluded.name,
                    enabled         = excluded.enabled,
                    status          = excluded.status,
                    parameters_json = excluded.parameters_json,
                    max_hold_hours  = excluded.max_hold_hours,
                    updated_at      = excluded.updated_at
                """,
                (
                    strategy.strategy_id,
                    strategy.name,
                    1 if strategy.enabled else 0,
                    strategy.status,
                    strategy.parameters_json,
                    strategy.max_hold_hours,
                    strategy.created_at,
                    now,
                ),
            )

    def get(self, strategy_id: str) -> V2Strategy | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM v2_strategies WHERE strategy_id = ?", (strategy_id,)
            ).fetchone()
        return _row_to_strategy(row) if row else None

    def list_enabled(self) -> list[V2Strategy]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_strategies WHERE enabled = 1 ORDER BY strategy_id"
            ).fetchall()
        return [_row_to_strategy(r) for r in rows]

    def set_enabled(self, strategy_id: str, enabled: bool) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "UPDATE v2_strategies SET enabled = ?, updated_at = ? WHERE strategy_id = ?",
                (1 if enabled else 0, now, strategy_id),
            )


def _row_to_strategy(row: sqlite3.Row) -> V2Strategy:
    return V2Strategy(
        strategy_id=row["strategy_id"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        status=row["status"],
        parameters_json=row["parameters_json"],
        max_hold_hours=row["max_hold_hours"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def register_hotlist_momentum_v2(db_path: Path | str) -> V2Strategy:
    """Bootstrap: upsert hotlist_momentum_v2 into the registry."""
    repo = V2StrategyRepository(db_path)
    repo.ensure_table()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    strategy = V2Strategy(
        strategy_id="hotlist_momentum_v2",
        name="Hotlist Momentum V2",
        enabled=True,
        status="PAPER",
        parameters_json=json.dumps({
            "max_open_orders": 3,
            "max_hold_hours": 24,
            "min_move_pct": "15",
            "min_quote_volume": "5000000",
            "min_rr": "2",
            "max_stop_pct": "5",
            "blacklist": [],
        }),
        max_hold_hours=24,
        created_at=now,
        updated_at=now,
    )
    repo.upsert(strategy)
    return strategy
