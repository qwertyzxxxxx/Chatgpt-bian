import json
import sqlite3
import logging
from datetime import date as _date
from market_hunter.config import DB_PATH, COOLDOWN_DAYS

logger = logging.getLogger(__name__)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables, unique indexes, and run column migrations."""
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS scan_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT NOT NULL,
        market TEXT NOT NULL DEFAULT 'US',
        total_scanned INTEGER,
        valid_price_count INTEGER,
        rejected_bad_price_count INTEGER,
        total_signals INTEGER,
        duration_seconds REAL,
        status TEXT DEFAULT 'completed',
        error TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_run_id INTEGER REFERENCES scan_runs(id),
        symbol TEXT NOT NULL,
        company_name TEXT,
        sector TEXT,
        industry TEXT,
        market_cap REAL,
        signal_date TEXT NOT NULL,
        close_price REAL,
        volume REAL,
        trend_score REAL,
        relative_strength_score REAL,
        volume_score REAL,
        pullback_risk_score REAL,
        sector_score REAL,
        total_score REAL,
        strategies TEXT,
        diagnostics TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS strategy_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER REFERENCES signals(id),
        strategy_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        signal_date TEXT NOT NULL,
        rank_in_strategy INTEGER,
        details TEXT,
        reason TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER REFERENCES signals(id),
        symbol TEXT NOT NULL,
        signal_date TEXT NOT NULL,
        signal_price REAL,
        eval_date_5d TEXT,
        return_5d REAL,
        eval_date_10d TEXT,
        return_10d REAL,
        eval_date_20d TEXT,
        return_20d REAL,
        max_drawdown REAL,
        max_gain REAL,
        evaluated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    -- V1.2: Telegram push tracking (re-push deduplication)
    CREATE TABLE IF NOT EXISTS signal_push_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        signal_date TEXT NOT NULL,
        action_status TEXT,
        total_score REAL,
        pushed_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, strategy_name, signal_date)
    );

    -- V1.2: Paper fund holdings filter (Part 5 / Part 8 preparation)
    CREATE TABLE IF NOT EXISTS paper_holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL UNIQUE,
        added_date TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()

    for ddl in [
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_signals_symbol_date
           ON signals(symbol, signal_date)""",
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_strategy_results_symbol_strategy_date
           ON strategy_results(symbol, strategy_name, signal_date)""",
    ]:
        try:
            cur.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError as e:
            logger.warning(f"Could not create unique index: {e}")

    # Backward-compatible column migrations
    for table, col, col_type in [
        ("signals",          "diagnostics",               "TEXT"),
        ("strategy_results", "reason",                    "TEXT"),
        ("scan_runs",        "valid_price_count",         "INTEGER"),
        ("scan_runs",        "rejected_bad_price_count",  "INTEGER"),
    ]:
        _migrate_add_column(cur, conn, table, col, col_type)

    conn.close()
    logger.info("Database initialized")


def _migrate_add_column(cur, conn, table: str, column: str, col_type: str):
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def insert_scan_run(run_date: str, total_scanned: int, total_signals: int,
                    duration: float, status: str = "completed", error: str = None,
                    valid_price_count: int = 0,
                    rejected_bad_price_count: int = 0) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO scan_runs
           (run_date, market, total_scanned, valid_price_count, rejected_bad_price_count,
            total_signals, duration_seconds, status, error)
           VALUES (?, 'US', ?, ?, ?, ?, ?, ?, ?)""",
        (run_date, total_scanned, valid_price_count, rejected_bad_price_count,
         total_signals, duration, status, error)
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def upsert_signal(scan_run_id: int, signal: dict) -> int:
    conn = get_conn()
    cur = conn.cursor()

    diagnostics_json = None
    if signal.get("diagnostics") is not None:
        try:
            diagnostics_json = json.dumps(signal["diagnostics"])
        except Exception:
            pass

    cur.execute(
        """INSERT OR IGNORE INTO signals
           (scan_run_id, symbol, company_name, sector, industry, market_cap, signal_date,
            close_price, volume, trend_score, relative_strength_score, volume_score,
            pullback_risk_score, sector_score, total_score, strategies, diagnostics)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            scan_run_id,
            signal.get("symbol"),
            signal.get("company_name"),
            signal.get("sector"),
            signal.get("industry"),
            signal.get("market_cap"),
            signal.get("signal_date"),
            signal.get("close_price"),
            signal.get("volume"),
            signal.get("trend_score"),
            signal.get("relative_strength_score"),
            signal.get("volume_score"),
            signal.get("pullback_risk_score"),
            signal.get("sector_score"),
            signal.get("total_score"),
            ",".join(signal.get("strategies", [])),
            diagnostics_json,
        )
    )
    conn.commit()

    if cur.lastrowid:
        signal_id = cur.lastrowid
    else:
        cur.execute(
            "SELECT id FROM signals WHERE symbol=? AND signal_date=?",
            (signal.get("symbol"), signal.get("signal_date")),
        )
        row = cur.fetchone()
        signal_id = row["id"] if row else 0

    # Enrich diagnostics when caller has richer data (e.g. action_status)
    if diagnostics_json and signal_id:
        cur.execute(
            """UPDATE signals
               SET diagnostics = ?
               WHERE id = ? AND (diagnostics IS NULL OR diagnostics = '{}')""",
            (diagnostics_json, signal_id),
        )
        conn.commit()

    conn.close()
    return signal_id


def insert_strategy_result(signal_id: int, strategy_name: str, symbol: str,
                            signal_date: str, rank: int, details: str = "",
                            reason: str = ""):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT OR IGNORE INTO strategy_results
           (signal_id, strategy_name, symbol, signal_date, rank_in_strategy, details, reason)
           VALUES (?,?,?,?,?,?,?)""",
        (signal_id, strategy_name, symbol, signal_date, rank, details, reason)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# V1.2 — Cooldown, Holding Filter, Re-Push
# ---------------------------------------------------------------------------

def is_in_cooldown(symbol: str, strategy_name: str, scan_date: str) -> bool:
    """
    True if the same (symbol, strategy) was signalled within the cooldown window.
    Uses strategy_results to find the last signal date before today.
    """
    days = COOLDOWN_DAYS.get(strategy_name, 10)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT MAX(signal_date) FROM strategy_results
           WHERE symbol = ? AND strategy_name = ? AND signal_date < ?""",
        (symbol, strategy_name, scan_date),
    )
    row = cur.fetchone()
    conn.close()
    last_str = row[0] if row else None
    if not last_str:
        return False
    delta = _date.fromisoformat(scan_date) - _date.fromisoformat(last_str)
    return delta.days < days


def is_holding(symbol: str) -> bool:
    """True if symbol is currently in the paper_holdings table."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM paper_holdings WHERE symbol = ?", (symbol,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def should_push_telegram(
    symbol: str,
    strategy_name: str,
    signal_date: str,
    action_status: str,
    total_score: float,
) -> bool:
    """
    True = send to Telegram.  False = DB-only.

    Push when:
      • First appearance (no prior push record)
      • action_status changed
      • score increased by >= 10 points
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT action_status, total_score FROM signal_push_log
           WHERE symbol = ? AND strategy_name = ? AND signal_date = ?""",
        (symbol, strategy_name, signal_date),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return True                                   # first appearance

    last_status = row[0]
    last_score  = float(row[1]) if row[1] is not None else 0.0

    if last_status != action_status:
        return True                                   # status changed

    if total_score >= last_score + 10:
        return True                                   # score jumped

    return False


def record_push(
    symbol: str,
    strategy_name: str,
    signal_date: str,
    action_status: str,
    total_score: float,
) -> None:
    """Record (or update) a Telegram push for re-push deduplication."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO signal_push_log
               (symbol, strategy_name, signal_date, action_status, total_score, pushed_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(symbol, strategy_name, signal_date) DO UPDATE SET
               action_status = excluded.action_status,
               total_score   = excluded.total_score,
               pushed_at     = datetime('now')""",
        (symbol, strategy_name, signal_date, action_status, total_score),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_unevaluated_signals(days_old_min: int = 5) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT s.id, s.symbol, s.signal_date, s.close_price
           FROM signals s
           LEFT JOIN evaluations e ON e.signal_id = s.id
           WHERE e.id IS NULL
             AND julianday('now') - julianday(s.signal_date) >= ?
           ORDER BY s.signal_date ASC""",
        (days_old_min,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def insert_evaluation(signal_id: int, eval_data: dict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO evaluations
           (signal_id, symbol, signal_date, signal_price,
            eval_date_5d, return_5d, eval_date_10d, return_10d,
            eval_date_20d, return_20d, max_drawdown, max_gain)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            signal_id,
            eval_data.get("symbol"),
            eval_data.get("signal_date"),
            eval_data.get("signal_price"),
            eval_data.get("eval_date_5d"),
            eval_data.get("return_5d"),
            eval_data.get("eval_date_10d"),
            eval_data.get("return_10d"),
            eval_data.get("eval_date_20d"),
            eval_data.get("return_20d"),
            eval_data.get("max_drawdown"),
            eval_data.get("max_gain"),
        )
    )
    conn.commit()
    conn.close()


def get_recent_signals(limit: int = 100) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT s.*, e.return_5d, e.return_10d, e.return_20d, e.max_drawdown, e.max_gain
           FROM signals s
           LEFT JOIN evaluations e ON e.signal_id = s.id
           ORDER BY s.signal_date DESC, s.total_score DESC
           LIMIT ?""",
        (limit,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_scan_runs(limit: int = 20) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM scan_runs ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_strategy_performance(strategy_name: str = None) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()

    where = ""
    params: tuple = ()
    if strategy_name:
        where = "WHERE sr.strategy_name = ?"
        params = (strategy_name,)

    cur.execute(
        f"""
        SELECT
            sr.strategy_name,
            COUNT(*)                                    AS total_trades,
            COUNT(e.return_20d)                         AS evaluated_count,
            SUM(CASE WHEN e.return_20d > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(AVG(e.return_5d),  2)                 AS avg_return_5d,
            ROUND(AVG(e.return_10d), 2)                 AS avg_return_10d,
            ROUND(AVG(e.return_20d), 2)                 AS avg_return_20d,
            ROUND(AVG(e.max_drawdown), 2)               AS avg_max_drawdown,
            ROUND(AVG(e.max_gain), 2)                   AS avg_max_gain,
            ROUND(MIN(e.return_20d), 2)                 AS worst_return,
            ROUND(MAX(e.return_20d), 2)                 AS best_return
        FROM strategy_results sr
        JOIN signals s ON s.id = sr.signal_id
        LEFT JOIN evaluations e ON e.signal_id = sr.signal_id
        {where}
        GROUP BY sr.strategy_name
        ORDER BY sr.strategy_name
        """,
        params,
    )
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        eval_cnt = d.get("evaluated_count") or 0
        wins     = d.get("wins") or 0
        d["win_rate_pct"] = round(wins / eval_cnt * 100, 1) if eval_cnt > 0 else None
        rows.append(d)
    conn.close()
    return rows
