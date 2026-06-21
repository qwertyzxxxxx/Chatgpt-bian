from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import Candidate, CommitteeDecision

_DDL = """
CREATE TABLE IF NOT EXISTS gemini_committee_reviews (
    review_id       TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'gemini',
    decision        TEXT NOT NULL,
    best_symbol     TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry           TEXT NOT NULL,
    stop_loss       TEXT NOT NULL,
    tp1             TEXT NOT NULL,
    tp2             TEXT NOT NULL,
    rr              TEXT NOT NULL,
    rating          TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    should_trade    INTEGER NOT NULL,
    data_quality    TEXT NOT NULL,
    raw_prompt_hash TEXT NOT NULL,
    raw_response    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS gemini_committee_candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id   TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    source      TEXT NOT NULL,
    direction   TEXT NOT NULL,
    entry       TEXT NOT NULL,
    stop_loss   TEXT NOT NULL,
    tp1         TEXT NOT NULL,
    tp2         TEXT NOT NULL,
    rr          TEXT NOT NULL,
    current_price TEXT NOT NULL,
    change_24h  TEXT NOT NULL,
    quote_volume TEXT NOT NULL,
    rank_order  INTEGER NOT NULL
);
"""


class CommitteeRepository:
    def __init__(self, db_path: str) -> None:
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._con.execute(stmt)
        try:
            self._con.execute(
                "ALTER TABLE gemini_committee_reviews ADD COLUMN reasons TEXT NOT NULL DEFAULT '[]'"
            )
            self._con.commit()
        except Exception:
            pass

    def close(self) -> None:
        self._con.close()

    def save_review(
        self,
        review_id: str,
        decision: CommitteeDecision,
        prompt_hash: str,
        model: str,
    ) -> None:
        import json as _json
        self._con.execute(
            """
            INSERT OR REPLACE INTO gemini_committee_reviews
              (review_id, created_at, provider, decision, best_symbol, direction,
               entry, stop_loss, tp1, tp2, rr, rating, risk_level, should_trade,
               data_quality, raw_prompt_hash, raw_response, status, reasons)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                review_id,
                datetime.now(timezone.utc).isoformat(),
                f"gemini/{model}",
                decision.decision,
                decision.best_symbol,
                decision.direction,
                decision.entry,
                decision.stop_loss,
                decision.tp1,
                decision.tp2,
                decision.rr,
                decision.rating,
                decision.risk_level,
                1 if decision.should_trade else 0,
                decision.data_quality,
                prompt_hash,
                decision.raw_response[:4000],
                "OPEN",
                _json.dumps(getattr(decision, "reasons", []), ensure_ascii=False),
            ),
        )
        self._con.commit()

    def save_candidates(self, review_id: str, candidates: list[Candidate]) -> None:
        rows = [
            (
                review_id,
                c.symbol,
                c.source,
                c.direction,
                c.entry,
                c.stop_loss,
                c.tp1,
                c.tp2,
                c.rr,
                c.current_price,
                c.change_24h,
                c.quote_volume,
                i,
            )
            for i, c in enumerate(candidates)
        ]
        self._con.executemany(
            """
            INSERT INTO gemini_committee_candidates
              (review_id, symbol, source, direction, entry, stop_loss, tp1, tp2, rr,
               current_price, change_24h, quote_volume, rank_order)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        self._con.commit()

    def last_review_at(self) -> datetime | None:
        row = self._con.execute(
            "SELECT created_at FROM gemini_committee_reviews ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["created_at"])

    def has_open_trade_recommendation(self) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM gemini_committee_reviews WHERE decision='TRADE' AND status='OPEN' LIMIT 1"
        ).fetchone()
        return row is not None

    def all_reviews(self) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM gemini_committee_reviews ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_reviews(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM gemini_committee_reviews ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def candidates_for_reviews(self, review_ids: list[str]) -> list[dict[str, Any]]:
        if not review_ids:
            return []
        placeholders = ",".join("?" * len(review_ids))
        rows = self._con.execute(
            f"SELECT * FROM gemini_committee_candidates WHERE review_id IN ({placeholders}) ORDER BY review_id, rank_order",
            review_ids,
        ).fetchall()
        return [dict(r) for r in rows]
