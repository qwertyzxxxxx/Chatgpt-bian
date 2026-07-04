"""V3 Feature Store — PostgreSQL backend.

Persists 40-100 factors per signal for future AI training.
Schema is intentionally flexible (JSON) so strategies can evolve factor sets.

Factors saved per signal:
  change24h, quote_volume, volume_ratio, ATR, EMA20, EMA60, EMA200,
  RSI, MACD, ADX, BBW, Funding, OI, MarketRegime, Sector, Rank,
  RepeatCount, plus any strategy-specific extras.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from binance_ai_trader.v3.storage.pg import get_conn


@dataclass(frozen=True, slots=True)
class V3FeatureRecord:
    signal_id: str
    strategy_id: str
    created_at: str
    features: dict


class V3FeatureStoreRepository:
    """All operations go to PostgreSQL."""

    def __init__(self, db_path=None) -> None:
        pass

    def save(self, signal_id: str, strategy_id: str, features: dict) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO v3_feature_store
                       (signal_id, strategy_id, created_at, features_json)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (signal_id) DO UPDATE
                         SET features_json = EXCLUDED.features_json,
                             created_at    = EXCLUDED.created_at""",
                    (signal_id, strategy_id, now, json.dumps(features, default=str)),
                )
            conn.commit()
        finally:
            conn.close()

    def load(self, signal_id: str) -> V3FeatureRecord | None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT signal_id, strategy_id, created_at, features_json FROM v3_feature_store WHERE signal_id=%s",
                    (signal_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return V3FeatureRecord(
            signal_id=row[0],
            strategy_id=row[1],
            created_at=row[2],
            features=json.loads(row[3]),
        )

    def load_by_strategy(self, strategy_id: str, limit: int = 1000) -> list[V3FeatureRecord]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT signal_id, strategy_id, created_at, features_json
                       FROM v3_feature_store
                       WHERE strategy_id=%s
                       ORDER BY created_at DESC LIMIT %s""",
                    (strategy_id, limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [
            V3FeatureRecord(
                signal_id=r[0],
                strategy_id=r[1],
                created_at=r[2],
                features=json.loads(r[3]),
            )
            for r in rows
        ]
