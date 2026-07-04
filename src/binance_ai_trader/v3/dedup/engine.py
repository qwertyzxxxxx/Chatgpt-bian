"""V3 Dedup Engine — prevents duplicate signals (PostgreSQL backend).

Checks v3_candidates in PostgreSQL for recent same-symbol+direction signals.
Default window: 24h.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from binance_ai_trader.v3.storage.pg import get_conn

log = logging.getLogger(__name__)

_VALID_WINDOWS_HOURS = {4, 12, 24, 48}
_DEFAULT_WINDOW_HOURS = 24


@dataclass(frozen=True, slots=True)
class DedupDecision:
    is_dup: bool
    reason: str


class V3DedupEngine:
    def __init__(self, db_path: Path | str = None) -> None:
        pass

    def check(
        self,
        strategy_id: str,
        symbol: str,
        direction: str,
        window_hours: int = _DEFAULT_WINDOW_HOURS,
        cross_strategy: bool = False,
    ) -> DedupDecision:
        if window_hours not in _VALID_WINDOWS_HOURS:
            log.warning("[V3Dedup] invalid window_hours=%d → default %d", window_hours, _DEFAULT_WINDOW_HOURS)
            window_hours = _DEFAULT_WINDOW_HOURS

        cutoff = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat(timespec="seconds")

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                if cross_strategy:
                    cur.execute(
                        """SELECT signal_id FROM v3_candidates
                           WHERE symbol=%s AND direction=%s AND created_at>=%s
                             AND status NOT IN ('BLOCKED','DEDUP')
                           LIMIT 1""",
                        (symbol, direction, cutoff),
                    )
                else:
                    cur.execute(
                        """SELECT signal_id FROM v3_candidates
                           WHERE strategy_id=%s AND symbol=%s AND direction=%s
                             AND created_at>=%s
                             AND status NOT IN ('BLOCKED','DEDUP')
                           LIMIT 1""",
                        (strategy_id, symbol, direction, cutoff),
                    )
                row = cur.fetchone()
        finally:
            conn.close()

        if row is None:
            return DedupDecision(False, "ok")

        scope = "cross-strategy" if cross_strategy else strategy_id
        return DedupDecision(
            True,
            f"duplicate {symbol}/{direction} within {window_hours}h ({scope}), existing={row[0]}",
        )
