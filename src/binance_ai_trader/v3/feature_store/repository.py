"""V3 Feature Store — persists 40-100 factors per signal for future AI training.

Every candidate that enters the pipeline saves its raw features here.
Features are stored as JSON; the schema is intentionally flexible so
individual strategies can evolve their factor sets independently.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS v3_feature_store (
    signal_id    TEXT PRIMARY KEY,
    strategy_id  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    features_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v3_feat_strategy
    ON v3_feature_store(strategy_id, created_at);
"""


@dataclass(frozen=True, slots=True)
class V3FeatureRecord:
    signal_id: str
    strategy_id: str
    created_at: str
    features: dict


class V3FeatureStoreRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.executescript(_DDL)

    def save(self, signal_id: str, strategy_id: str, features: dict) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO v3_feature_store
                   (signal_id, strategy_id, created_at, features_json)
                   VALUES (?,?,?,?)""",
                (signal_id, strategy_id, now, json.dumps(features, default=str)),
            )

    def load(self, signal_id: str) -> V3FeatureRecord | None:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM v3_feature_store WHERE signal_id=?", (signal_id,)
            ).fetchone()
        if row is None:
            return None
        return V3FeatureRecord(
            signal_id=row["signal_id"],
            strategy_id=row["strategy_id"],
            created_at=row["created_at"],
            features=json.loads(row["features_json"]),
        )

    def load_by_strategy(self, strategy_id: str, limit: int = 1000) -> list[V3FeatureRecord]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM v3_feature_store
                   WHERE strategy_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (strategy_id, limit),
            ).fetchall()
        return [
            V3FeatureRecord(
                signal_id=r["signal_id"],
                strategy_id=r["strategy_id"],
                created_at=r["created_at"],
                features=json.loads(r["features_json"]),
            )
            for r in rows
        ]
