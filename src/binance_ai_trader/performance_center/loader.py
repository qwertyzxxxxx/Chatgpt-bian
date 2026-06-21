from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import List

from .models import (
    StrategyResult,
    STRATEGY_HOTLIST, STRATEGY_AI_MACRO, STRATEGY_GEMINI,
    RESULT_OPEN, RESULT_TP1, RESULT_TP2, RESULT_SL, RESULT_TIMEOUT,
)

log = logging.getLogger(__name__)

_SIGNAL_RESULT_MAP: dict[str, str] = {
    "TP1_HIT": RESULT_TP1,
    "WIN_TP2": RESULT_TP2,
    "LOSS": RESULT_SL,
    "EXPIRED": RESULT_TIMEOUT,
}


def _rows(db_path: str, sql: str, params: tuple = ()) -> list:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _safe_str(v) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


def load_hotlist(db_path: str = "data/market_data.db") -> List[StrategyResult]:
    rows = _rows(db_path, "SELECT * FROM hotlist_opportunities ORDER BY created_at")
    results = []
    for r in rows:
        source_id = f"hotlist_{r['id']}"
        results.append(StrategyResult(
            result_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, source_id)),
            strategy=STRATEGY_HOTLIST,
            symbol=_safe_str(r["symbol"]),
            direction=_safe_str(r["direction"]),
            entry=_safe_str(r["entry"]),
            stop_loss=_safe_str(r["sl"]),
            tp1=_safe_str(r["tp1"]),
            tp2=_safe_str(r["tp2"]),
            opened_at=_safe_str(r["created_at"]),
            source_id=source_id,
            result=RESULT_OPEN,
        ))
    return results


def load_ai_macro(db_path: str = "data/ai_macro.db") -> List[StrategyResult]:
    rows = _rows(db_path, "SELECT * FROM ai_macro_trades ORDER BY created_at")
    results = []
    for r in rows:
        source_id = str(r["trade_id"])
        existing_result = _safe_str(r["status"]) if r["status"] else RESULT_OPEN
        pnl = None
        if r["pnl_pct"] is not None:
            try:
                pnl = float(r["pnl_pct"])
            except (ValueError, TypeError):
                pnl = None
        results.append(StrategyResult(
            result_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, source_id)),
            strategy=STRATEGY_AI_MACRO,
            symbol=_safe_str(r["symbol"]),
            direction=_safe_str(r["direction"]),
            entry=_safe_str(r["entry"]),
            stop_loss=_safe_str(r["stop_loss"]),
            tp1=_safe_str(r["tp1"]),
            tp2=_safe_str(r["tp2"]),
            opened_at=_safe_str(r["created_at"]),
            source_id=source_id,
            closed_at=r["closed_at"] if r["closed_at"] else None,
            result=existing_result,
            pnl_pct=pnl,
        ))
    return results


def load_gemini_committee(db_path: str = "data/market_data.db") -> List[StrategyResult]:
    rows = _rows(
        db_path,
        "SELECT * FROM gemini_committee_reviews WHERE should_trade=1 ORDER BY created_at",
    )
    results = []
    for r in rows:
        source_id = str(r["review_id"])
        existing_result = _safe_str(r["status"]) if r["status"] else RESULT_OPEN
        results.append(StrategyResult(
            result_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, source_id)),
            strategy=STRATEGY_GEMINI,
            symbol=_safe_str(r["best_symbol"]),
            direction=_safe_str(r["direction"]),
            entry=_safe_str(r["entry"]),
            stop_loss=_safe_str(r["stop_loss"]),
            tp1=_safe_str(r["tp1"]),
            tp2=_safe_str(r["tp2"]),
            opened_at=_safe_str(r["created_at"]),
            source_id=source_id,
            result=existing_result,
        ))
    return results


def load_paper_trades(db_path: str = "data/market_data.db") -> List[StrategyResult]:
    """Load signal-strategy paper-trade results, one StrategyResult per evaluation.

    Joins signal_evaluations → signals → analysis_snapshots to resolve the
    strategy_id (baseline_v1, breakout_hunter_v1, …) and LEFT JOINs paper_trades
    for realized_r / risk_pct when available.  Missing tables are silently ignored
    so the function is safe against fresh / test databases.
    """
    sql = """
        SELECT a.strategy_id,
               e.signal_run_id,
               e.symbol,
               e.direction,
               e.result      AS eval_result,
               e.entry,
               e.stop_loss,
               e.tp1,
               e.tp2,
               s.generated_at,
               p.realized_r,
               p.risk_pct
        FROM signal_evaluations e
        JOIN signals s
          ON s.run_id = e.signal_run_id AND s.symbol = e.symbol
        JOIN analysis_snapshots a
          ON a.snapshot_id = s.snapshot_id
        LEFT JOIN paper_trades p
          ON p.signal_run_id = e.signal_run_id AND p.symbol = e.symbol
        ORDER BY s.generated_at
    """
    try:
        rows = _rows(db_path, sql)
    except Exception as exc:
        log.debug("load_paper_trades: skipped (%s)", exc)
        return []

    results = []
    for r in rows:
        source_id = f"paper_{r['signal_run_id']}_{r['symbol']}"
        mapped = _SIGNAL_RESULT_MAP.get(_safe_str(r["eval_result"]), RESULT_OPEN)

        rr: float | None = None
        pnl_pct: float | None = None
        if r["realized_r"] is not None:
            try:
                rr = float(r["realized_r"])
                if r["risk_pct"] is not None:
                    pnl_pct = round(rr * float(r["risk_pct"]), 6)
            except (ValueError, TypeError):
                rr = None

        results.append(StrategyResult(
            result_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, source_id)),
            strategy=_safe_str(r["strategy_id"]),
            symbol=_safe_str(r["symbol"]),
            direction=_safe_str(r["direction"]),
            entry=_safe_str(r["entry"]),
            stop_loss=_safe_str(r["stop_loss"]),
            tp1=_safe_str(r["tp1"]),
            tp2=_safe_str(r["tp2"]),
            opened_at=_safe_str(r["generated_at"]),
            source_id=source_id,
            result=mapped,
            rr_realized=rr,
            pnl_pct=pnl_pct,
        ))
    return results


def load_all(
    market_db: str = "data/market_data.db",
    ai_macro_db: str = "data/ai_macro.db",
) -> List[StrategyResult]:
    results = []
    results.extend(load_hotlist(market_db))
    results.extend(load_ai_macro(ai_macro_db))
    results.extend(load_gemini_committee(market_db))
    results.extend(load_paper_trades(market_db))
    return results
