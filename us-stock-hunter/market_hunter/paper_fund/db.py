"""
Paper Fund database — independent tables, never touches scanner tables.

Tables:
  paper_fund       — fund configuration and cash balance (single row)
  paper_positions  — open and closed positions
  paper_orders     — pending buy orders (filled next-day open)
  paper_trades     — every buy/sell transaction
  paper_equity     — daily equity snapshots
"""

import sqlite3
import logging
from market_hunter.config import DB_PATH

logger = logging.getLogger(__name__)

INITIAL_CAPITAL = 100_000.0


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_paper_db():
    """Create all paper-fund tables if they don't exist."""
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS paper_fund (
        id              INTEGER PRIMARY KEY DEFAULT 1,
        initial_capital REAL    NOT NULL DEFAULT 100000.0,
        current_cash    REAL    NOT NULL DEFAULT 100000.0,
        created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
        updated_at      TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS paper_positions (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol              TEXT    NOT NULL,
        strategy_name       TEXT,
        entry_date          TEXT    NOT NULL,
        entry_price         REAL    NOT NULL,
        shares              INTEGER NOT NULL,
        cost_basis          REAL    NOT NULL,
        stop_loss           REAL    NOT NULL,
        target1             REAL    NOT NULL,
        target2             REAL,
        rr_ratio            REAL,
        risk_pct            REAL,
        score               REAL,
        partial_sold        INTEGER DEFAULT 0,
        partial_sold_price  REAL,
        partial_sold_date   TEXT,
        partial_sold_pnl    REAL,
        status              TEXT    DEFAULT 'open',
        close_date          TEXT,
        close_price         REAL,
        close_reason        TEXT,
        created_at          TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS paper_orders (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol         TEXT    NOT NULL,
        strategy_name  TEXT,
        order_date     TEXT    NOT NULL,
        trigger_price  REAL    NOT NULL,
        cancel_limit   REAL    NOT NULL,
        stop_loss      REAL    NOT NULL,
        target1        REAL    NOT NULL,
        target2        REAL,
        rr_ratio       REAL,
        risk_pct       REAL,
        score          REAL,
        planned_shares INTEGER,
        planned_cost   REAL,
        status         TEXT    DEFAULT 'pending',
        fill_date      TEXT,
        fill_price     REAL,
        cancel_reason  TEXT,
        created_at     TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS paper_trades (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol       TEXT    NOT NULL,
        trade_type   TEXT    NOT NULL,
        trade_date   TEXT    NOT NULL,
        price        REAL    NOT NULL,
        shares       INTEGER NOT NULL,
        amount       REAL    NOT NULL,
        cost_basis   REAL,
        pnl          REAL,
        pnl_pct      REAL,
        reason       TEXT,
        position_id  INTEGER REFERENCES paper_positions(id),
        order_id     INTEGER REFERENCES paper_orders(id),
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS paper_equity (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        equity_date         TEXT    NOT NULL UNIQUE,
        cash                REAL    NOT NULL,
        position_value      REAL    NOT NULL DEFAULT 0,
        total_equity        REAL    NOT NULL,
        spy_close           REAL,
        pnl_daily           REAL,
        pnl_cumulative      REAL,
        pnl_cumulative_pct  REAL,
        spy_return_pct      REAL,
        alpha               REAL,
        created_at          TEXT    DEFAULT CURRENT_TIMESTAMP
    );
    """)
    c.commit()
    c.close()
    logger.debug("Paper fund DB initialized")


# ---------------------------------------------------------------------------
# Fund (single-row config/cash table)
# ---------------------------------------------------------------------------

def get_fund() -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM paper_fund WHERE id = 1").fetchone()
    c.close()
    return dict(row) if row else None


def init_fund() -> dict:
    """Insert initial fund row if not present. Returns current fund state."""
    init_paper_db()
    c = _conn()
    c.execute(
        """INSERT OR IGNORE INTO paper_fund (id, initial_capital, current_cash)
           VALUES (1, ?, ?)""",
        (INITIAL_CAPITAL, INITIAL_CAPITAL),
    )
    c.commit()
    row = c.execute("SELECT * FROM paper_fund WHERE id = 1").fetchone()
    c.close()
    return dict(row)


def update_fund_cash(new_cash: float):
    c = _conn()
    c.execute(
        "UPDATE paper_fund SET current_cash = ?, updated_at = datetime('now') WHERE id = 1",
        (round(new_cash, 2),),
    )
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def get_open_positions() -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM paper_positions WHERE status = 'open' ORDER BY entry_date"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_position_by_symbol(symbol: str) -> dict | None:
    c = _conn()
    row = c.execute(
        "SELECT * FROM paper_positions WHERE symbol = ? AND status = 'open'", (symbol,)
    ).fetchone()
    c.close()
    return dict(row) if row else None


def insert_position(data: dict) -> int:
    c = _conn()
    cur = c.execute(
        """INSERT INTO paper_positions
           (symbol, strategy_name, entry_date, entry_price, shares, cost_basis,
            stop_loss, target1, target2, rr_ratio, risk_pct, score)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data["symbol"], data.get("strategy_name"), data["entry_date"],
            data["entry_price"], data["shares"], data["cost_basis"],
            data["stop_loss"], data["target1"], data.get("target2"),
            data.get("rr_ratio"), data.get("risk_pct"), data.get("score"),
        ),
    )
    pos_id = cur.lastrowid
    c.commit()
    c.close()
    return pos_id


def update_position(pos_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [pos_id]
    c = _conn()
    c.execute(f"UPDATE paper_positions SET {sets} WHERE id = ?", vals)
    c.commit()
    c.close()


def close_position(pos_id: int, close_date: str, close_reason: str, close_price: float):
    update_position(
        pos_id,
        status="closed",
        close_date=close_date,
        close_reason=close_reason,
        close_price=close_price,
        shares=0,
    )


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def get_pending_orders() -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM paper_orders WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def has_pending_order(symbol: str) -> bool:
    c = _conn()
    row = c.execute(
        "SELECT 1 FROM paper_orders WHERE symbol = ? AND status = 'pending'", (symbol,)
    ).fetchone()
    c.close()
    return row is not None


def insert_order(data: dict) -> int:
    c = _conn()
    cur = c.execute(
        """INSERT INTO paper_orders
           (symbol, strategy_name, order_date, trigger_price, cancel_limit,
            stop_loss, target1, target2, rr_ratio, risk_pct, score,
            planned_shares, planned_cost)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data["symbol"], data.get("strategy_name"), data["order_date"],
            data["trigger_price"], data["cancel_limit"],
            data["stop_loss"], data["target1"], data.get("target2"),
            data.get("rr_ratio"), data.get("risk_pct"), data.get("score"),
            data.get("planned_shares"), data.get("planned_cost"),
        ),
    )
    oid = cur.lastrowid
    c.commit()
    c.close()
    return oid


def update_order(order_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [order_id]
    c = _conn()
    c.execute(f"UPDATE paper_orders SET {sets} WHERE id = ?", vals)
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def insert_trade(data: dict) -> int:
    c = _conn()
    cur = c.execute(
        """INSERT INTO paper_trades
           (symbol, trade_type, trade_date, price, shares, amount,
            cost_basis, pnl, pnl_pct, reason, position_id, order_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data["symbol"], data["trade_type"], data["trade_date"],
            data["price"], data["shares"], data["amount"],
            data.get("cost_basis"), data.get("pnl"), data.get("pnl_pct"),
            data.get("reason"), data.get("position_id"), data.get("order_id"),
        ),
    )
    tid = cur.lastrowid
    c.commit()
    c.close()
    return tid


def get_trades_for_date(trade_date: str) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM paper_trades WHERE trade_date = ? ORDER BY created_at",
        (trade_date,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_all_trades(limit: int = 200) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM paper_trades ORDER BY trade_date DESC, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Equity snapshots
# ---------------------------------------------------------------------------

def get_latest_equity() -> dict | None:
    c = _conn()
    row = c.execute(
        "SELECT * FROM paper_equity ORDER BY equity_date DESC LIMIT 1"
    ).fetchone()
    c.close()
    return dict(row) if row else None


def get_earliest_equity() -> dict | None:
    c = _conn()
    row = c.execute(
        "SELECT * FROM paper_equity ORDER BY equity_date ASC LIMIT 1"
    ).fetchone()
    c.close()
    return dict(row) if row else None


def get_equity_history(days: int = 30) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM paper_equity ORDER BY equity_date DESC LIMIT ?", (days,)
    ).fetchall()
    c.close()
    return [dict(r) for r in reversed(rows)]


def upsert_equity(data: dict):
    c = _conn()
    c.execute(
        """INSERT INTO paper_equity
               (equity_date, cash, position_value, total_equity, spy_close,
                pnl_daily, pnl_cumulative, pnl_cumulative_pct, spy_return_pct, alpha)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(equity_date) DO UPDATE SET
               cash               = excluded.cash,
               position_value     = excluded.position_value,
               total_equity       = excluded.total_equity,
               spy_close          = excluded.spy_close,
               pnl_daily          = excluded.pnl_daily,
               pnl_cumulative     = excluded.pnl_cumulative,
               pnl_cumulative_pct = excluded.pnl_cumulative_pct,
               spy_return_pct     = excluded.spy_return_pct,
               alpha              = excluded.alpha""",
        (
            data["equity_date"], data["cash"], data["position_value"],
            data["total_equity"], data.get("spy_close"),
            data.get("pnl_daily"), data.get("pnl_cumulative"),
            data.get("pnl_cumulative_pct"), data.get("spy_return_pct"),
            data.get("alpha"),
        ),
    )
    c.commit()
    c.close()
