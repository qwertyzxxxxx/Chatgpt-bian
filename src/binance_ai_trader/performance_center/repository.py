from __future__ import annotations

import sqlite3
from typing import List, Optional

from .models import StrategyResult, RESULT_OPEN

_DDL = """
CREATE TABLE IF NOT EXISTS strategy_results (
    result_id         TEXT PRIMARY KEY,
    strategy          TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    direction         TEXT NOT NULL,
    entry             TEXT NOT NULL,
    stop_loss         TEXT NOT NULL,
    tp1               TEXT NOT NULL,
    tp2               TEXT NOT NULL,
    opened_at         TEXT NOT NULL,
    closed_at         TEXT,
    result            TEXT NOT NULL DEFAULT 'OPEN',
    pnl_pct           REAL,
    rr_realized       REAL,
    duration_minutes  INTEGER,
    source_id         TEXT NOT NULL
);
"""


class PerformanceRepository:
    def __init__(self, db_path: str = "data/market_data.db") -> None:
        self._db = db_path
        self._ensure_table()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db)
        con.row_factory = sqlite3.Row
        return con

    def _ensure_table(self) -> None:
        with self._conn() as con:
            con.executescript(_DDL)

    def upsert(self, sr: StrategyResult) -> None:
        sql = """
        INSERT INTO strategy_results
            (result_id, strategy, symbol, direction, entry, stop_loss, tp1, tp2,
             opened_at, closed_at, result, pnl_pct, rr_realized, duration_minutes, source_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(result_id) DO UPDATE SET
            closed_at        = excluded.closed_at,
            result           = excluded.result,
            pnl_pct          = excluded.pnl_pct,
            rr_realized      = excluded.rr_realized,
            duration_minutes = excluded.duration_minutes
        """
        with self._conn() as con:
            con.execute(sql, (
                sr.result_id, sr.strategy, sr.symbol, sr.direction,
                sr.entry, sr.stop_loss, sr.tp1, sr.tp2,
                sr.opened_at, sr.closed_at, sr.result,
                sr.pnl_pct, sr.rr_realized, sr.duration_minutes,
                sr.source_id,
            ))

    def bulk_upsert(self, results: List[StrategyResult]) -> int:
        for sr in results:
            self.upsert(sr)
        return len(results)

    def _fetch(self, sql: str, params: tuple = ()) -> List[StrategyResult]:
        with self._conn() as con:
            cur = con.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [StrategyResult.from_row(tuple(r), cols) for r in rows]

    def get_open(self, strategy: Optional[str] = None) -> List[StrategyResult]:
        if strategy:
            return self._fetch(
                "SELECT * FROM strategy_results WHERE result=? AND strategy=?",
                (RESULT_OPEN, strategy),
            )
        return self._fetch(
            "SELECT * FROM strategy_results WHERE result=?",
            (RESULT_OPEN,),
        )

    def get_all(self, strategy: Optional[str] = None) -> List[StrategyResult]:
        if strategy:
            return self._fetch(
                "SELECT * FROM strategy_results WHERE strategy=? ORDER BY opened_at",
                (strategy,),
            )
        return self._fetch("SELECT * FROM strategy_results ORDER BY opened_at")

    def source_id_exists(self, source_id: str) -> bool:
        with self._conn() as con:
            r = con.execute(
                "SELECT 1 FROM strategy_results WHERE source_id=? LIMIT 1", (source_id,)
            ).fetchone()
        return r is not None

    def get_since(self, since_iso: str, strategy: str | None = None) -> List[StrategyResult]:
        if strategy:
            return self._fetch(
                "SELECT * FROM strategy_results WHERE opened_at >= ? AND strategy = ? ORDER BY opened_at",
                (since_iso, strategy),
            )
        return self._fetch(
            "SELECT * FROM strategy_results WHERE opened_at >= ? ORDER BY opened_at",
            (since_iso,),
        )

    def get_closed_since(self, since_iso: str) -> List[StrategyResult]:
        return self._fetch(
            "SELECT * FROM strategy_results WHERE closed_at >= ? AND result != 'OPEN' ORDER BY closed_at",
            (since_iso,),
        )

    def update_settled(self, sr: StrategyResult) -> None:
        sql = """
        UPDATE strategy_results
        SET result=?, pnl_pct=?, rr_realized=?, duration_minutes=?, closed_at=?
        WHERE result_id=?
        """
        with self._conn() as con:
            con.execute(sql, (
                sr.result, sr.pnl_pct, sr.rr_realized,
                sr.duration_minutes, sr.closed_at, sr.result_id,
            ))
