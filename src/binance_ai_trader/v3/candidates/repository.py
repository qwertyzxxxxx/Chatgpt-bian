"""V3 Candidate Pool — PostgreSQL backend.

Every strategy writes here first. Signal IDs are assigned atomically.
Signal ID format:  PREFIX-YYYYMMDD-NNNNNN
  HOT   hotlist_*
  MON   monster_*
  BRK   breakout_*
  BER   bear_*
  AIX   ai_macro / future_ai_*
  GEN   (fallback)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from binance_ai_trader.v3.storage.pg import get_conn

_STRATEGY_PREFIXES: dict[str, str] = {
    "reversal":    "REV",
    "hotlist":     "HOT",
    "monster":     "MON",
    "breakout":    "BRK",
    "bear":        "BER",
    "ai_macro":    "AIX",
    "future_ai":   "AIX",
    "classic_c1":  "CLN",
    "classic_c2":  "CLN",
    "classic_c3":  "CLN",
    "classic_c4":  "CLN",
    "classic_k1":   "CLK",
    "classic_k2":   "CLK",
    "classic_k3":   "CLK",
    "classic_k4":   "CLK",
    "classic_k3v2": "CLK",
    "classic_k4v2": "CLK",
    "classic":     "CLN",
    "rsd_long":    "RSD",
    "rsd_short":   "RSD",
    "rsd":         "RSD",
}


def _strategy_prefix(strategy_id: str) -> str:
    sid = strategy_id.lower()
    for key, prefix in _STRATEGY_PREFIXES.items():
        if sid.startswith(key) or key in sid:
            return prefix
    return "GEN"


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
    score_total: int | None = None
    score_grade: str | None = None
    score_version: str | None = None
    volume_score: int | None = None
    trend_structure_score: int | None = None
    entry_position_score: int | None = None
    risk_reward_score: int | None = None
    strategy_fit_score: int | None = None
    score_summary: str | None = None
    scored_at: str | None = None


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
    meta_json: str = "{}"


class V3CandidateRepository:
    """All operations go to PostgreSQL."""

    def __init__(self, db_path=None) -> None:
        # db_path retained for backward compatibility; ignored (PG is used)
        pass

    def generate_signal_id(self, strategy_id: str, now: datetime | None = None) -> str:
        prefix = _strategy_prefix(strategy_id)
        date_str = (now or datetime.now(UTC)).strftime("%Y%m%d")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("BEGIN")
                cur.execute(
                    "SELECT next_seq FROM v3_signal_id_seq WHERE prefix=%s AND date=%s FOR UPDATE",
                    (prefix, date_str),
                )
                row = cur.fetchone()
                if row is None:
                    seq = 1
                    cur.execute(
                        "INSERT INTO v3_signal_id_seq(prefix, date, next_seq) VALUES(%s,%s,%s)",
                        (prefix, date_str, 2),
                    )
                else:
                    seq = row[0]
                    cur.execute(
                        "UPDATE v3_signal_id_seq SET next_seq=%s WHERE prefix=%s AND date=%s",
                        (seq + 1, prefix, date_str),
                    )
            conn.commit()
        finally:
            conn.close()
        return f"{prefix}-{date_str}-{seq:06d}"

    def save(
        self,
        inp: CandidateInput,
        signal_id: str,
        status: str = "PENDING",
        reason_override: str | None = None,
    ) -> V3Candidate:
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
            reason=reason_override if reason_override is not None else inp.reason,
            status=status,
            repeat_count=0,
        )
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO v3_candidates
                       (signal_id, strategy_id, created_at, symbol, direction,
                        entry, sl, tp1, tp2, rr, confidence, stop_pct, change_24h,
                        quote_volume, volume_ratio, atr, ema20, ema60,
                        market_regime, reason, status, repeat_count)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (signal_id) DO NOTHING""",
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
            conn.commit()
        finally:
            conn.close()
        return candidate

    def update_status(self, signal_id: str, status: str) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE v3_candidates SET status=%s WHERE signal_id=%s",
                    (status, signal_id),
                )
            conn.commit()
        finally:
            conn.close()

    def load_by_id(self, signal_id: str) -> V3Candidate | None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM v3_candidates WHERE signal_id=%s", (signal_id,))
                row = cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.close()
        return _row_to_candidate(dict(zip(cols, row))) if row else None

    def load_pending(self) -> list[V3Candidate]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM v3_candidates WHERE status='PENDING' ORDER BY created_at"
                )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.close()
        return [_row_to_candidate(dict(zip(cols, r))) for r in rows]

    def load_recent(self, hours: int = 24) -> list[V3Candidate]:
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM v3_candidates WHERE created_at >= %s ORDER BY created_at DESC",
                    (cutoff,),
                )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.close()
        return [_row_to_candidate(dict(zip(cols, r))) for r in rows]

    def exists_recent(
        self,
        strategy_id: str,
        symbol: str,
        direction: str,
        hours: int = 24,
    ) -> bool:
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT 1 FROM v3_candidates
                       WHERE strategy_id=%s AND symbol=%s AND direction=%s AND created_at>=%s
                       LIMIT 1""",
                    (strategy_id, symbol, direction, cutoff),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return row is not None

    def increment_repeat_count(self, signal_id: str) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE v3_candidates SET repeat_count = repeat_count + 1 WHERE signal_id=%s",
                    (signal_id,),
                )
            conn.commit()
        finally:
            conn.close()


    def save_score(self, signal_id: str, score) -> None:
        """Persist unified score fields onto an existing v3_candidates row.

        score: UnifiedScore from binance_ai_trader.v3.scoring.models.
        Silently ignores DB errors — scoring never blocks signal push.
        """
        import json
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE v3_candidates
                       SET score_total=%s, score_grade=%s, score_version=%s,
                           volume_score=%s, trend_structure_score=%s,
                           entry_position_score=%s, risk_reward_score=%s,
                           strategy_fit_score=%s,
                           score_summary=%s, score_details_json=%s, scored_at=%s
                       WHERE signal_id=%s""",
                    (
                        score.score_total, score.score_grade, score.score_version,
                        score.volume_score, score.trend_structure_score,
                        score.entry_position_score, score.risk_reward_score,
                        score.strategy_fit_score,
                        score.score_summary,
                        json.dumps(score.score_details, default=str),
                        score.scored_at,
                        signal_id,
                    ),
                )
            conn.commit()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug(
                "[repo] save_score failed for %s: %s", signal_id, exc
            )
        finally:
            conn.close()


def _row_to_candidate(row: dict) -> V3Candidate:
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
        score_total=row.get("score_total"),
        score_grade=row.get("score_grade"),
        score_version=row.get("score_version"),
        volume_score=row.get("volume_score"),
        trend_structure_score=row.get("trend_structure_score"),
        entry_position_score=row.get("entry_position_score"),
        risk_reward_score=row.get("risk_reward_score"),
        strategy_fit_score=row.get("strategy_fit_score"),
        score_summary=row.get("score_summary"),
        scored_at=row.get("scored_at"),
    )
