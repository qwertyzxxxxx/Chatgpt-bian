"""Wave Watchlist SQLite Repository — 两阶段之间的持久化状态。

Table: wave_watchlist
  watch_id          TEXT PK
  symbol            TEXT
  strategy_id       TEXT    -- 'wave_long' | 'wave_short'
  direction         TEXT    -- 'LONG' | 'SHORT'
  platform_high     TEXT    -- Decimal as string
  platform_low      TEXT
  breakout_close    TEXT
  breakout_vol_ratio TEXT
  triggered_at      TEXT    -- ISO8601, 1H K线收盘时间
  triggered_at_ms   INTEGER -- ms timestamp，用于过滤15M K线
  expires_at        TEXT    -- triggered_at + 8h
  status            TEXT    -- WATCHING | ENTERED | EXPIRED | INVALIDATED
  created_at        TEXT
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS wave_watchlist (
    watch_id           TEXT PRIMARY KEY,
    symbol             TEXT NOT NULL,
    strategy_id        TEXT NOT NULL,
    direction          TEXT NOT NULL,
    platform_high      TEXT NOT NULL,
    platform_low       TEXT NOT NULL,
    breakout_close     TEXT NOT NULL,
    breakout_vol_ratio TEXT NOT NULL,
    triggered_at       TEXT NOT NULL,
    triggered_at_ms    INTEGER NOT NULL,
    expires_at         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'WATCHING',
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wave_wl_strat_status
    ON wave_watchlist (strategy_id, status);
"""


@dataclass(frozen=True, slots=True)
class WaveWatchItem:
    watch_id:           str
    symbol:             str
    strategy_id:        str
    direction:          str
    platform_high:      Decimal
    platform_low:       Decimal
    breakout_close:     Decimal
    breakout_vol_ratio: Decimal
    triggered_at:       str
    triggered_at_ms:    int
    expires_at:         str
    status:             str
    created_at:         str


def _row_to_item(r: tuple) -> WaveWatchItem:
    return WaveWatchItem(
        watch_id=r[0], symbol=r[1], strategy_id=r[2], direction=r[3],
        platform_high=Decimal(r[4]), platform_low=Decimal(r[5]),
        breakout_close=Decimal(r[6]), breakout_vol_ratio=Decimal(r[7]),
        triggered_at=r[8], triggered_at_ms=int(r[9]),
        expires_at=r[10], status=r[11], created_at=r[12],
    )


class WaveWatchlistRepo:
    """Thread-safe SQLite repo (一个连接 per 调用)。"""

    def __init__(self, db_path: Path) -> None:
        self._db = str(db_path)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db, timeout=10)

    def _init(self) -> None:
        with self._conn() as con:
            con.executescript(_DDL)

    # ── writes ─────────────────────────────────────────────────────────────

    def add(
        self,
        symbol: str,
        strategy_id: str,
        direction: str,
        platform_high: Decimal,
        platform_low: Decimal,
        breakout_close: Decimal,
        breakout_vol_ratio: Decimal,
        triggered_at: str,
        triggered_at_ms: int,
        watch_hours: int = 8,
    ) -> str:
        watch_id  = str(uuid.uuid4())[:16]
        now       = datetime.now(UTC)
        expires   = (datetime.fromisoformat(triggered_at).replace(tzinfo=UTC)
                     + timedelta(hours=watch_hours)).isoformat(timespec="seconds")
        with self._conn() as con:
            con.execute(
                """INSERT INTO wave_watchlist
                   (watch_id, symbol, strategy_id, direction,
                    platform_high, platform_low, breakout_close, breakout_vol_ratio,
                    triggered_at, triggered_at_ms, expires_at, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (watch_id, symbol, strategy_id, direction,
                 str(platform_high), str(platform_low),
                 str(breakout_close), str(breakout_vol_ratio),
                 triggered_at, triggered_at_ms, expires,
                 "WATCHING", now.isoformat(timespec="seconds")),
            )
        return watch_id

    def mark_entered(self, watch_id: str) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE wave_watchlist SET status='ENTERED' WHERE watch_id=?",
                (watch_id,),
            )

    def invalidate(self, watch_id: str) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE wave_watchlist SET status='INVALIDATED' WHERE watch_id=?",
                (watch_id,),
            )

    def expire_old(self, now: datetime) -> None:
        now_str = now.isoformat(timespec="seconds")
        cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")
        with self._conn() as con:
            con.execute(
                "UPDATE wave_watchlist SET status='EXPIRED' "
                "WHERE status='WATCHING' AND expires_at < ?",
                (now_str,),
            )
            con.execute(
                "DELETE FROM wave_watchlist "
                "WHERE status IN ('EXPIRED','ENTERED','INVALIDATED') AND created_at < ?",
                (cutoff,),
            )

    # ── reads ──────────────────────────────────────────────────────────────

    def get_active(self, strategy_id: str) -> list[WaveWatchItem]:
        with self._conn() as con:
            rows = con.execute(
                """SELECT watch_id, symbol, strategy_id, direction,
                          platform_high, platform_low, breakout_close, breakout_vol_ratio,
                          triggered_at, triggered_at_ms, expires_at, status, created_at
                   FROM wave_watchlist
                   WHERE strategy_id=? AND status='WATCHING'
                   ORDER BY triggered_at""",
                (strategy_id,),
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    def is_watching(self, strategy_id: str, symbol: str) -> bool:
        with self._conn() as con:
            row = con.execute(
                "SELECT 1 FROM wave_watchlist "
                "WHERE strategy_id=? AND symbol=? AND status='WATCHING'",
                (strategy_id, symbol),
            ).fetchone()
        return row is not None
