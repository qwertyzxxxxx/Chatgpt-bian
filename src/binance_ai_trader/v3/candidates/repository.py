"""V3 Candidate Pool — unified entry point for all strategies.

Every strategy writes here first.  Nothing else is written until
Risk + Dedup gates pass.

Signal ID format:  PREFIX-YYYYMMDD-NNNNNN
  HOT   hotlist_momentum_v2 / hotlist_*
  MON   monster_*
  BRK   breakout_*
  BER   bear_*
  AIX   ai_macro / future_ai_*
  GEN   (fallback for unknown strategies)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_STRATEGY_PREFIXES: dict[str, str] = {
    "hotlist": "HOT",
    "monster": "MON",
    "breakout": "BRK",
    "bear": "BER",
    "ai_macro": "AIX",
    "future_ai": "AIX",
}

_SEQ_DDL = """
CREATE TABLE IF NOT EXISTS v3_signal_id_seq (
    prefix  TEXT    NOT NULL,
    date    TEXT    NOT NULL,
    next_seq INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (prefix, date)
);
"""

_CANDIDATES_DDL = """
CREATE TABLE IF NOT EXISTS v3_candidates (
    signal_id       TEXT PRIMARY KEY,
    strategy_id     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry           TEXT NOT NULL,
    sl              TEXT NOT NULL,
    tp1             TEXT NOT NULL,
    tp2             TEXT,
    rr              TEXT NOT NULL,
    confidence      REAL,
    stop_pct        REAL,
    change_24h      REAL,
    quote_volume    REAL,
    volume_ratio    REAL,
    atr             REAL,
    ema20           REAL,
    ema60           REAL,
    market_regime   TEXT,
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    repeat_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_v3_cand_symbol_dir
    ON v3_candidates(symbol, direction, created_at);
CREATE INDEX IF NOT EXISTS idx_v3_cand_strategy
    ON v3_candidates(strategy_id, created_at);
CREATE INDEX IF NOT EXISTS idx_v3_cand_status
    ON v3_candidates(status);
"""


@dataclass(frozen=True, slots=True)
class V3Candidate:
    signal_id: str
    strategy_id: str
    created_at: str
    symbol: str
    direction: str
    entry: str
    sl: str
    tp1: str
    tp2: str | None
    rr: str
    confidence: float | None
    stop_pct: float | None
    change_24h: float | None
    quote_volume: float | None
    volume_ratio: float | None
    atr: float | None
    ema20: float | None
    ema60: float | None
    market_regime: str | None
    reason: str | None
    status: str
    repeat_count: int


@dataclass(frozen=True, slots=True)
class CandidateInput:
    """What a strategy returns — no signal_id yet (assigned by repository)."""
    strategy_id: str
    symbol: str
    direction: str
    entry: str
    sl: str
    tp1: str
    tp2: str | None
    rr: str
    confidence: float | None = None
    stop_pct: float | None = None
    change_24h: float | None = None
    quote_volume: float | None = None
    volume_ratio: float | None = None
    atr: float | None = None
    ema20: float | None = None
    ema60: float | None = None
    market_regime: str | None = None
    reason: str | None = None


def _strategy_prefix(strategy_id: str) -> str:
    sid = strategy_id.lower()
    for key, prefix in _STRATEGY_PREFIXES.items():
        if sid.startswith(key) or key in sid:
            return prefix
    return "GEN"


class V3CandidateRepository:
    def __init__(self, db_path: Path | str) -> None:
        self._db = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.executescript(_SEQ_DDL)
            conn.executescript(_CANDIDATES_DDL)

    # ------------------------------------------------------------------
    # Signal ID generation — thread-safe via BEGIN IMMEDIATE
    # ------------------------------------------------------------------

    def generate_signal_id(self, strategy_id: str, now: datetime | None = None) -> str:
        prefix = _strategy_prefix(strategy_id)
        date_str = (now or datetime.now(UTC)).strftime("%Y%m%d")
        with sqlite3.connect(self._db, isolation_level=None) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT next_seq FROM v3_signal_id_seq WHERE prefix=? AND date=?",
                (prefix, date_str),
            ).fetchone()
            if row is None:
                seq = 1
                conn.execute(
                    "INSERT INTO v3_signal_id_seq(prefix, date, next_seq) VALUES(?,?,?)",
                    (prefix, date_str, 2),
                )
            else:
                seq = row[0]
                conn.execute(
                    "UPDATE v3_signal_id_seq SET next_seq=? WHERE prefix=? AND date=?",
                    (seq + 1, prefix, date_str),
                )
            conn.execute("COMMIT")
        return f"{prefix}-{date_str}-{seq:06d}"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, inp: CandidateInput, signal_id: str, status: str = "PENDING") -> V3Candidate:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        candidate = V3Candidate(
            signal_id=signal_id,
            strategy_id=inp.strategy_id,
            created_at=now,
            symbol=inp.symbol,
            direction=inp.direction,
            entry=inp.entry,
            sl=inp.sl,
            tp1=inp.tp1,
            tp2=inp.tp2,
            rr=inp.rr,
            confidence=inp.confidence,
            stop_pct=inp.stop_pct,
            change_24h=inp.change_24h,
            quote_volume=inp.quote_volume,
            volume_ratio=inp.volume_ratio,
            atr=inp.atr,
            ema20=inp.ema20,
            ema60=inp.ema60,
            market_regime=inp.market_regime,
            reason=inp.reason,
            status=status,
            repeat_count=0,
        )
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO v3_candidates
                   (signal_id, strategy_id, created_at, symbol, direction,
                    entry, sl, tp1, tp2, rr, confidence, stop_pct, change_24h,
                    quote_volume, volume_ratio, atr, ema20, ema60,
                    market_regime, reason, status, repeat_count)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate.signal_id, candidate.strategy_id, candidate.created_at,
                    candidate.symbol, candidate.direction, candidate.entry, candidate.sl,
                    candidate.tp1, candidate.tp2, candidate.rr, candidate.confidence,
                    candidate.stop_pct, candidate.change_24h, candidate.quote_volume,
                    candidate.volume_ratio, candidate.atr, candidate.ema20, candidate.ema60,
                    candidate.market_regime, candidate.reason, candidate.status,
                    candidate.repeat_count,
                ),
            )
        return candidate

    def update_status(self, signal_id: str, status: str) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "UPDATE v3_candidates SET status=? WHERE signal_id=?",
                (status, signal_id),
            )

    def load_by_id(self, signal_id: str) -> V3Candidate | None:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM v3_candidates WHERE signal_id=?", (signal_id,)
            ).fetchone()
        return _row_to_candidate(row) if row else None

    def load_pending(self) -> list[V3Candidate]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM v3_candidates WHERE status='PENDING' ORDER BY created_at"
            ).fetchall()
        return [_row_to_candidate(r) for r in rows]

    def load_recent(self, hours: int = 24) -> list[V3Candidate]:
        from datetime import timedelta
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM v3_candidates WHERE created_at >= ? ORDER BY created_at DESC",
                (cutoff,),
            ).fetchall()
        return [_row_to_candidate(r) for r in rows]

    def exists_recent(
        self,
        strategy_id: str,
        symbol: str,
        direction: str,
        hours: int = 24,
    ) -> bool:
        from datetime import timedelta
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                """SELECT 1 FROM v3_candidates
                   WHERE strategy_id=? AND symbol=? AND direction=? AND created_at>=?
                   LIMIT 1""",
                (strategy_id, symbol, direction, cutoff),
            ).fetchone()
        return row is not None

    def increment_repeat_count(self, signal_id: str) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "UPDATE v3_candidates SET repeat_count = repeat_count + 1 WHERE signal_id=?",
                (signal_id,),
            )


def _row_to_candidate(row: sqlite3.Row) -> V3Candidate:
    return V3Candidate(
        signal_id=row["signal_id"],
        strategy_id=row["strategy_id"],
        created_at=row["created_at"],
        symbol=row["symbol"],
        direction=row["direction"],
        entry=row["entry"],
        sl=row["sl"],
        tp1=row["tp1"],
        tp2=row["tp2"],
        rr=row["rr"],
        confidence=row["confidence"],
        stop_pct=row["stop_pct"],
        change_24h=row["change_24h"],
        quote_volume=row["quote_volume"],
        volume_ratio=row["volume_ratio"],
        atr=row["atr"],
        ema20=row["ema20"],
        ema60=row["ema60"],
        market_regime=row["market_regime"],
        reason=row["reason"],
        status=row["status"],
        repeat_count=row["repeat_count"],
    )
