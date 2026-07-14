"""Classic scan records repository — PostgreSQL.

Persists every evaluated coin per cycle, whether or not a signal was generated.
"""
from __future__ import annotations

import logging

from binance_ai_trader.classic.models import ScanRecord
from binance_ai_trader.v3.storage.pg import get_conn

log = logging.getLogger(__name__)


class ClassicScanRepository:
    def save_records(self, records: list[ScanRecord]) -> int:
        if not records:
            return 0
        saved = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                for rec in records:
                    try:
                        cur.execute(
                            """
                            INSERT INTO classic_scan_records (
                                scan_id, strategy_id, scanned_at, symbol,
                                pool_type, pool_rank, direction,
                                change_24h, quote_volume, change_3d, change_7d,
                                range_pos_30d, consec_days, trend_4h, atr_dist_4h,
                                vol_ratio_1h, vol_ratio_15m, vol_grade,
                                price_pattern, score, passed,
                                entry, sl, tp1, tp2, rr,
                                rejection, signal_id
                            ) VALUES (
                                %s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s,
                                %s,%s,%s,%s, %s,%s,%s, %s,%s,%s,
                                %s,%s,%s,%s,%s, %s,%s
                            )
                            ON CONFLICT (scan_id) DO NOTHING
                            """,
                            (
                                rec.scan_id, rec.strategy_id, rec.scanned_at, rec.symbol,
                                rec.pool_type, rec.pool_rank, rec.direction,
                                rec.change_24h, rec.quote_volume, rec.change_3d, rec.change_7d,
                                rec.range_pos_30d, rec.consec_days, rec.trend_4h, rec.atr_dist_4h,
                                rec.vol_ratio_1h, rec.vol_ratio_15m, rec.vol_grade,
                                rec.price_pattern, rec.score, rec.passed,
                                rec.entry, rec.sl, rec.tp1, rec.tp2, rec.rr,
                                rec.rejection, rec.signal_id,
                            ),
                        )
                        saved += 1
                    except Exception as exc:
                        log.warning("[ClassicRepo] save failed for %s/%s: %s",
                                    rec.strategy_id, rec.symbol, exc)
            conn.commit()
        return saved

    def update_signal_id(self, scan_id: str, signal_id: str) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE classic_scan_records SET signal_id=%s WHERE scan_id=%s",
                    (signal_id, scan_id),
                )
            conn.commit()

    def exists_open_24h(self, symbol: str, direction: str, strategy_id: str,
                        since_iso: str) -> bool:
        """True if we already emitted a signal for this symbol/direction within 24h."""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM classic_scan_records
                    WHERE symbol=%s AND direction=%s AND strategy_id=%s
                      AND passed=TRUE AND signal_id IS NOT NULL
                      AND scanned_at >= %s
                    LIMIT 1
                    """,
                    (symbol, direction, strategy_id, since_iso),
                )
                return cur.fetchone() is not None
