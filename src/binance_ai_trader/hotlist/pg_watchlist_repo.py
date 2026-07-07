"""PostgreSQL-backed watchlist repository for V66 strategy.

Provides the same interface as HotlistWatchlistRepository (SQLite) but
stores state in the shared PostgreSQL database so it survives redeployments.

Only the methods used by HotlistWatchlist.review() are implemented:
  - expire_before()
  - load()
  - save()
  - active()
"""
from __future__ import annotations

import logging

from binance_ai_trader.hotlist.models import HotlistWatchlistItem
from binance_ai_trader.v3.storage.pg import get_conn

log = logging.getLogger(__name__)

_SELECT_COLS = """
    symbol, source, first_seen_at, last_seen_at, expires_at,
    observation_count, last_rank, status
"""


def _row(row: tuple) -> HotlistWatchlistItem:
    return HotlistWatchlistItem(
        symbol=str(row[0]),
        source=str(row[1]),
        first_seen_at=str(row[2]),
        last_seen_at=str(row[3]),
        expires_at=str(row[4]),
        observation_count=int(row[5]),
        last_rank=int(row[6]),
        status=str(row[7]),
    )


class V66WatchlistPgRepository:
    """PostgreSQL watchlist store for V66 — state survives redeployments."""

    def expire_before(self, timestamp: str) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE v66_watchlist SET status='EXPIRED'
                       WHERE status='ACTIVE' AND expires_at <= %s""",
                    (timestamp,),
                )
            conn.commit()
        finally:
            conn.close()

    def load(self, symbol: str) -> HotlistWatchlistItem | None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_COLS} FROM v66_watchlist WHERE symbol=%s",
                    (symbol,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return _row(row) if row else None

    def save(self, item: HotlistWatchlistItem) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO v66_watchlist (
                           symbol, source, first_seen_at, last_seen_at, expires_at,
                           observation_count, last_rank, status
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (symbol) DO UPDATE SET
                           source=EXCLUDED.source,
                           first_seen_at=EXCLUDED.first_seen_at,
                           last_seen_at=EXCLUDED.last_seen_at,
                           expires_at=EXCLUDED.expires_at,
                           observation_count=EXCLUDED.observation_count,
                           last_rank=EXCLUDED.last_rank,
                           status=EXCLUDED.status""",
                    (
                        item.symbol,
                        item.source,
                        item.first_seen_at,
                        item.last_seen_at,
                        item.expires_at,
                        item.observation_count,
                        item.last_rank,
                        item.status,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def active(self) -> tuple[HotlistWatchlistItem, ...]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT {_SELECT_COLS} FROM v66_watchlist
                        WHERE status='ACTIVE'
                        ORDER BY last_rank, symbol"""
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return tuple(_row(r) for r in rows)
