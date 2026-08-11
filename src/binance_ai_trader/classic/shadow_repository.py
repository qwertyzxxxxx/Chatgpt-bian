"""Repository for classic_shadow_records — permanent shadow comparison data.

READ/WRITE: saves shadow decisions and links to source + shadow paper orders.
Never modifies v3_paper_orders or any production table.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from binance_ai_trader.v3.storage.pg import get_conn

log = logging.getLogger(__name__)


class ClassicShadowRepository:
    # ── Write ─────────────────────────────────────────────────────────────────

    def save_shadow(
        self,
        *,
        shadow_id:                 str,
        source_signal_id:          str,
        source_strategy:           str,
        shadow_strategy:           str,
        symbol:                    str,
        direction:                 str,
        signal_time:               str,
        decision:                  str,
        reject_reason:             str,
        shadow_order_id:           str | None = None,
        # K1 extras
        signal_candle_open:        float | None = None,
        signal_candle_close:       float | None = None,
        signal_candle_change_pct:  float | None = None,
        signal_candle_above_ema20: bool  | None = None,
        break_previous_high:       bool  | None = None,
        vol_ratio_15m:             float | None = None,
        # K2 extras
        range_position_30d:        float | None = None,
    ) -> None:
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO classic_shadow_records (
                        shadow_id, source_signal_id,
                        source_strategy, shadow_strategy,
                        symbol, direction, signal_time,
                        decision, reject_reason, shadow_order_id,
                        signal_candle_open, signal_candle_close,
                        signal_candle_change_pct, signal_candle_above_ema20,
                        break_previous_high, vol_ratio_15m,
                        range_position_30d, created_at
                    ) VALUES (
                        %s,%s, %s,%s, %s,%s,%s, %s,%s,%s,
                        %s,%s, %s,%s, %s,%s, %s,%s
                    )
                    ON CONFLICT (shadow_id) DO NOTHING
                    """,
                    (
                        shadow_id, source_signal_id,
                        source_strategy, shadow_strategy,
                        symbol, direction, signal_time,
                        decision, reject_reason, shadow_order_id,
                        signal_candle_open, signal_candle_close,
                        signal_candle_change_pct, signal_candle_above_ema20,
                        break_previous_high, vol_ratio_15m,
                        range_position_30d, created_at,
                    ),
                )
            conn.commit()
        log.info(
            "[Shadow] saved %s/%s → %s (%s) shadow_order=%s",
            source_strategy, symbol, decision, reject_reason or "OK", shadow_order_id,
        )

    def update_shadow_order_id(self, shadow_id: str, shadow_order_id: str) -> None:
        """Backfill shadow_order_id after the shadow paper order is created."""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE classic_shadow_records SET shadow_order_id=%s WHERE shadow_id=%s",
                    (shadow_order_id, shadow_id),
                )
            conn.commit()

    # ── Read (for /shadow Telegram command) ───────────────────────────────────

    def get_stats(self) -> list[dict[str, Any]]:
        """Return per-shadow-strategy aggregate stats."""
        sql = """
            SELECT
                sr.shadow_strategy,
                sr.source_strategy,
                COUNT(*)                                                       AS total_candidates,
                SUM(CASE WHEN sr.decision='PASS'   THEN 1 ELSE 0 END)         AS passed,
                SUM(CASE WHEN sr.decision='REJECT' THEN 1 ELSE 0 END)         AS rejected,
                -- source strategy paper order outcomes (linked via source_signal_id)
                SUM(CASE WHEN po_src.result='TP1' OR po_src.result='TP2'
                                THEN 1 ELSE 0 END)                             AS source_tp,
                SUM(CASE WHEN po_src.result='SL'  THEN 1 ELSE 0 END)          AS source_sl,
                -- how many source TP did shadow FILTER OUT (reject)
                SUM(CASE WHEN sr.decision='REJECT'
                     AND (po_src.result='TP1' OR po_src.result='TP2')
                                THEN 1 ELSE 0 END)                             AS filtered_tp,
                -- how many source SL did shadow FILTER OUT
                SUM(CASE WHEN sr.decision='REJECT'
                     AND po_src.result='SL'
                                THEN 1 ELSE 0 END)                             AS filtered_sl,
                -- shadow paper order outcomes (PASS trades only)
                SUM(CASE WHEN po_shd.result='TP1' OR po_shd.result='TP2'
                                THEN 1 ELSE 0 END)                             AS shadow_tp,
                SUM(CASE WHEN po_shd.result='SL'  THEN 1 ELSE 0 END)          AS shadow_sl,
                SUM(CASE WHEN po_shd.status='OPEN' THEN 1 ELSE 0 END)         AS shadow_open,
                AVG(CAST(po_shd.pnl_pct AS FLOAT))                            AS shadow_avg_pnl
            FROM classic_shadow_records sr
            LEFT JOIN v3_paper_orders po_src
                ON po_src.signal_id = sr.source_signal_id
            LEFT JOIN v3_paper_orders po_shd
                ON po_shd.order_id = sr.shadow_order_id
            GROUP BY sr.shadow_strategy, sr.source_strategy
            ORDER BY sr.shadow_strategy
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_source_stats(self, source_strategy: str) -> dict[str, Any]:
        """Return settled source-strategy paper order stats (WR, PF, Expectancy)."""
        sql = """
            SELECT
                COUNT(*)                                                        AS total,
                SUM(CASE WHEN result IN ('TP1','TP2') THEN 1 ELSE 0 END)       AS tp,
                SUM(CASE WHEN result='SL'             THEN 1 ELSE 0 END)       AS sl,
                AVG(CAST(pnl_pct AS FLOAT))                                    AS avg_pnl,
                AVG(CASE WHEN result IN ('TP1','TP2')
                     THEN CAST(pnl_pct AS FLOAT) END)                          AS avg_win,
                AVG(CASE WHEN result='SL'
                     THEN CAST(pnl_pct AS FLOAT) END)                          AS avg_loss
            FROM v3_paper_orders
            WHERE strategy_id=%s
              AND status IN ('TP1','TP2','SL','TIMEOUT','EXPIRED_NOT_FILLED')
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (source_strategy,))
                cols = [d[0] for d in cur.description]
                row  = cur.fetchone()
                return dict(zip(cols, row)) if row else {}

    def get_shadow_paper_stats(self, shadow_strategy: str) -> dict[str, Any]:
        """Return settled shadow paper order stats (WR, PF, Expectancy)."""
        sql = """
            SELECT
                COUNT(*)                                                        AS total,
                SUM(CASE WHEN result IN ('TP1','TP2') THEN 1 ELSE 0 END)       AS tp,
                SUM(CASE WHEN result='SL'             THEN 1 ELSE 0 END)       AS sl,
                AVG(CAST(pnl_pct AS FLOAT))                                    AS avg_pnl,
                AVG(CASE WHEN result IN ('TP1','TP2')
                     THEN CAST(pnl_pct AS FLOAT) END)                          AS avg_win,
                AVG(CASE WHEN result='SL'
                     THEN CAST(pnl_pct AS FLOAT) END)                          AS avg_loss
            FROM v3_paper_orders
            WHERE strategy_id=%s
              AND status IN ('TP1','TP2','SL','TIMEOUT','EXPIRED_NOT_FILLED')
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (shadow_strategy,))
                cols = [d[0] for d in cur.description]
                row  = cur.fetchone()
                return dict(zip(cols, row)) if row else {}

    def count_by_strategy(self, strategy_id: str) -> int:
        """Count settled paper orders for a strategy (for sample threshold checks)."""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM v3_paper_orders WHERE strategy_id=%s"
                    " AND status IN ('TP1','TP2','SL','TIMEOUT','EXPIRED_NOT_FILLED')",
                    (strategy_id,),
                )
                row = cur.fetchone()
                return row[0] if row else 0
