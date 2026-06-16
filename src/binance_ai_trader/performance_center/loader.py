from __future__ import annotations

import sqlite3
import uuid
from typing import List

from .models import (
    StrategyResult,
    STRATEGY_HOTLIST, STRATEGY_AI_MACRO, STRATEGY_GEMINI,
    RESULT_OPEN,
)


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


def load_all(
    market_db: str = "data/market_data.db",
    ai_macro_db: str = "data/ai_macro.db",
) -> List[StrategyResult]:
    results = []
    results.extend(load_hotlist(market_db))
    results.extend(load_ai_macro(ai_macro_db))
    results.extend(load_gemini_committee(market_db))
    return results
