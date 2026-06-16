from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.ai_macro.models import AIMacroTrade

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ai_macro_trades (
    trade_id   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    direction  TEXT NOT NULL,
    entry      TEXT NOT NULL,
    stop_loss  TEXT NOT NULL,
    tp1        TEXT NOT NULL,
    tp2        TEXT NOT NULL,
    score      INTEGER NOT NULL,
    market_state TEXT NOT NULL,
    risk_grade TEXT NOT NULL,
    reason     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'OPEN',
    pnl_pct    TEXT,
    closed_at  TEXT
)
"""


class AIMacroRepository:
    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(database))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save_trade(self, trade: AIMacroTrade) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO ai_macro_trades (
                    trade_id, created_at, symbol, direction, entry, stop_loss,
                    tp1, tp2, score, market_state, risk_grade, reason,
                    status, pnl_pct, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.trade_id, trade.created_at, trade.symbol, trade.direction,
                    str(trade.entry), str(trade.stop_loss), str(trade.tp1), str(trade.tp2),
                    trade.score, trade.market_state, trade.risk_grade, trade.reason,
                    trade.status,
                    str(trade.pnl_pct) if trade.pnl_pct is not None else None,
                    trade.closed_at,
                ),
            )

    def open_trades(self) -> tuple[AIMacroTrade, ...]:
        rows = self._conn.execute(
            """
            SELECT trade_id, created_at, symbol, direction, entry, stop_loss,
                   tp1, tp2, score, market_state, risk_grade, reason,
                   status, pnl_pct, closed_at
            FROM ai_macro_trades WHERE status='OPEN'
            ORDER BY created_at
            """
        ).fetchall()
        return tuple(_row(r) for r in rows)

    def open_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM ai_macro_trades WHERE status='OPEN'"
        ).fetchone()
        return int(row[0])

    def all_trades(self) -> tuple[AIMacroTrade, ...]:
        rows = self._conn.execute(
            """
            SELECT trade_id, created_at, symbol, direction, entry, stop_loss,
                   tp1, tp2, score, market_state, risk_grade, reason,
                   status, pnl_pct, closed_at
            FROM ai_macro_trades ORDER BY created_at
            """
        ).fetchall()
        return tuple(_row(r) for r in rows)

    def update_trade(
        self,
        trade_id: str,
        status: str,
        pnl_pct: Decimal,
        closed_at: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE ai_macro_trades
                SET status=?, pnl_pct=?, closed_at=?
                WHERE trade_id=?
                """,
                (status, str(pnl_pct), closed_at, trade_id),
            )


def _row(row: tuple) -> AIMacroTrade:
    return AIMacroTrade(
        trade_id=str(row[0]),
        created_at=str(row[1]),
        symbol=str(row[2]),
        direction=str(row[3]),
        entry=Decimal(str(row[4])),
        stop_loss=Decimal(str(row[5])),
        tp1=Decimal(str(row[6])),
        tp2=Decimal(str(row[7])),
        score=int(row[8]),
        market_state=str(row[9]),
        risk_grade=str(row[10]),
        reason=str(row[11]),
        status=str(row[12]),
        pnl_pct=Decimal(str(row[13])) if row[13] is not None else None,
        closed_at=str(row[14]) if row[14] is not None else None,
    )
