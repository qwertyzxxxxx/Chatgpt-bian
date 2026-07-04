"""V3 Dedup Engine — prevents duplicate signals across all strategies.

Dedup is checked against v3_candidates (symbol + direction within window).
Each strategy can configure its own window (4h / 12h / 24h / 48h).

Default window: 24h.

Returns DedupDecision(is_dup, reason).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_VALID_WINDOWS_HOURS = {4, 12, 24, 48}
_DEFAULT_WINDOW_HOURS = 24


@dataclass(frozen=True, slots=True)
class DedupDecision:
    is_dup: bool
    reason: str


class V3DedupEngine:
    """Stateless dedup gate.  Checks v3_candidates for recent same-symbol+direction."""

    def __init__(self, db_path: Path | str) -> None:
        self._db = str(db_path)

    def check(
        self,
        strategy_id: str,
        symbol: str,
        direction: str,
        window_hours: int = _DEFAULT_WINDOW_HOURS,
        cross_strategy: bool = False,
    ) -> DedupDecision:
        """Check if a duplicate candidate exists within the dedup window.

        Args:
            strategy_id:   Strategy that generated the candidate.
            symbol:        Trading pair.
            direction:     'LONG' or 'SHORT'.
            window_hours:  Lookback window (4 / 12 / 24 / 48).
            cross_strategy: If True, dedup across ALL strategies (not just this one).
        """
        if window_hours not in _VALID_WINDOWS_HOURS:
            log.warning(
                "[V3Dedup] invalid window_hours=%d, defaulting to %d",
                window_hours, _DEFAULT_WINDOW_HOURS,
            )
            window_hours = _DEFAULT_WINDOW_HOURS

        cutoff = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat(
            timespec="seconds"
        )

        with sqlite3.connect(self._db) as conn:
            if not _table_exists(conn, "v3_candidates"):
                return DedupDecision(False, "ok")

            if cross_strategy:
                row = conn.execute(
                    """SELECT signal_id FROM v3_candidates
                       WHERE symbol=? AND direction=? AND created_at>=?
                         AND status NOT IN ('BLOCKED','DEDUP')
                       LIMIT 1""",
                    (symbol, direction, cutoff),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT signal_id FROM v3_candidates
                       WHERE strategy_id=? AND symbol=? AND direction=?
                         AND created_at>=?
                         AND status NOT IN ('BLOCKED','DEDUP')
                       LIMIT 1""",
                    (strategy_id, symbol, direction, cutoff),
                ).fetchone()

        if row is None:
            return DedupDecision(False, "ok")

        scope = "cross-strategy" if cross_strategy else strategy_id
        return DedupDecision(
            True,
            f"duplicate {symbol}/{direction} within {window_hours}h ({scope}), "
            f"existing signal_id={row[0]}",
        )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None
