"""Feed paper_orders from strategy signal sources.

Sources currently supported:
  - hotlist alerts   → strategy_id='hotlist', pushed=True
  - hotlist opps     → strategy_id='hotlist', pushed=False
  - baseline_v1      → strategy_id='baseline_v1', pushed=False
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.paper.order_repository import PaperOrder, PaperOrderRepository

log = logging.getLogger(__name__)

_DEFAULT_EXPIRY_HOURS = 24


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _order_id(source_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"paper_order_{source_id}"))


def _expiry(created_at: str, hours: int = _DEFAULT_EXPIRY_HOURS) -> str:
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (dt + timedelta(hours=hours)).isoformat(timespec="seconds")
    except Exception:
        return (datetime.now(UTC) + timedelta(hours=hours)).isoformat(timespec="seconds")


def _safe_dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _safe_str(v) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


class PaperFeeder:
    """Reads signal sources and creates paper_orders for unseen signals."""

    def __init__(
        self,
        repo: PaperOrderRepository,
        database: Path,
        expiry_hours: int = _DEFAULT_EXPIRY_HOURS,
    ) -> None:
        self._repo = repo
        self._database = database
        self._expiry_hours = expiry_hours

    def feed_all(self) -> dict[str, int]:
        """Feed from all sources. Returns count of new orders per source."""
        results: dict[str, int] = {}
        try:
            results["hotlist_pushed"] = self._feed_hotlist_alerts()
        except Exception as exc:
            log.warning("feeder: hotlist_alerts failed: %s", exc)
            results["hotlist_pushed"] = 0
        try:
            results["hotlist_candidate"] = self._feed_hotlist_opportunities()
        except Exception as exc:
            log.warning("feeder: hotlist_opportunities failed: %s", exc)
            results["hotlist_candidate"] = 0
        try:
            results["baseline_v1"] = self._feed_baseline()
        except Exception as exc:
            log.warning("feeder: baseline failed: %s", exc)
            results["baseline_v1"] = 0
        return results

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._database))
        con.row_factory = sqlite3.Row
        return con

    def _table_exists(self, con: sqlite3.Connection, table: str) -> bool:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None

    def _feed_hotlist_alerts(self) -> int:
        con = self._con()
        try:
            if not self._table_exists(con, "hotlist_alerts"):
                return 0
            rows = con.execute(
                "SELECT id, symbol, direction, entry, stop_loss, tp1, tp2, rr,"
                "       created_at, expires_at, rank_type"
                " FROM hotlist_alerts ORDER BY created_at"
            ).fetchall()
        finally:
            con.close()

        count = 0
        for r in rows:
            source_id = f"hotlist_alert_{r['id']}"
            if self._repo.source_id_exists(source_id):
                continue
            entry = _safe_dec(r["entry"])
            sl = _safe_dec(r["stop_loss"])
            tp1 = _safe_dec(r["tp1"])
            tp2 = _safe_dec(r["tp2"])
            rr = _safe_dec(r["rr"])
            if not all([entry, sl, tp1, tp2]):
                continue

            expires_at = r["expires_at"] or _expiry(r["created_at"], self._expiry_hours)
            order = PaperOrder(
                order_id=_order_id(source_id),
                strategy_id="hotlist",
                source_type="hotlist",
                source_id=source_id,
                symbol=r["symbol"],
                direction=r["direction"],
                entry=entry,
                stop_loss=sl,
                tp1=tp1,
                tp2=tp2,
                rr=rr or Decimal("0"),
                status="OPEN",
                result=None,
                pushed=True,
                alert_id=str(r["id"]),
                created_at=r["created_at"],
                filled_at=None,
                closed_at=None,
                expires_at=expires_at,
                pnl_pct=None,
                rr_realized=None,
                duration_minutes=None,
                legacy=False,
            )
            self._repo.save(order)
            count += 1
        return count

    def _feed_hotlist_opportunities(self) -> int:
        con = self._con()
        try:
            if not self._table_exists(con, "hotlist_opportunities"):
                return 0
            rows = con.execute(
                "SELECT id, symbol, direction, entry, sl, tp1, tp2, rr,"
                "       created_at, expires_at"
                " FROM hotlist_opportunities ORDER BY created_at"
            ).fetchall()
        finally:
            con.close()

        count = 0
        for r in rows:
            source_id = f"hotlist_opp_{r['id']}"
            if self._repo.source_id_exists(source_id):
                continue
            entry = _safe_dec(r["entry"])
            sl = _safe_dec(r["sl"])
            tp1 = _safe_dec(r["tp1"])
            tp2 = _safe_dec(r["tp2"])
            rr = _safe_dec(r["rr"])
            if not all([entry, sl, tp1, tp2]):
                continue

            expires_at = r["expires_at"] or _expiry(r["created_at"], self._expiry_hours)
            order = PaperOrder(
                order_id=_order_id(source_id),
                strategy_id="hotlist",
                source_type="hotlist",
                source_id=source_id,
                symbol=r["symbol"],
                direction=r["direction"],
                entry=entry,
                stop_loss=sl,
                tp1=tp1,
                tp2=tp2,
                rr=rr or Decimal("0"),
                status="OPEN",
                result=None,
                pushed=False,
                alert_id=None,
                created_at=r["created_at"],
                filled_at=None,
                closed_at=None,
                expires_at=expires_at,
                pnl_pct=None,
                rr_realized=None,
                duration_minutes=None,
                legacy=False,
            )
            self._repo.save(order)
            count += 1
        return count

    def _feed_baseline(self) -> int:
        con = self._con()
        try:
            if not (
                self._table_exists(con, "signal_evaluations")
                and self._table_exists(con, "signals")
                and self._table_exists(con, "analysis_snapshots")
            ):
                return 0
            rows = con.execute(
                """
                SELECT a.strategy_id,
                       e.signal_run_id,
                       e.symbol,
                       e.direction,
                       e.entry,
                       e.stop_loss,
                       e.tp1,
                       e.tp2,
                       s.generated_at
                FROM signal_evaluations e
                JOIN signals s
                  ON s.run_id = e.signal_run_id AND s.symbol = e.symbol
                JOIN analysis_snapshots a
                  ON a.snapshot_id = s.snapshot_id
                WHERE a.strategy_id = 'baseline_v1'
                ORDER BY s.generated_at
                """
            ).fetchall()
        except Exception as exc:
            log.debug("feeder: baseline query failed: %s", exc)
            return 0
        finally:
            con.close()

        count = 0
        for r in rows:
            source_id = f"baseline_{r['signal_run_id']}_{r['symbol']}"
            if self._repo.source_id_exists(source_id):
                continue
            entry = _safe_dec(r["entry"])
            sl = _safe_dec(r["stop_loss"])
            tp1 = _safe_dec(r["tp1"])
            tp2 = _safe_dec(r["tp2"])
            if not all([entry, sl, tp1, tp2]):
                continue

            rr_val = Decimal("0")
            if sl and entry and sl != entry:
                risk = abs(entry - sl)
                if r["direction"] == "LONG":
                    rr_val = (tp1 - entry) / risk if tp1 else Decimal("0")
                else:
                    rr_val = (entry - tp1) / risk if tp1 else Decimal("0")

            created_at = r["generated_at"]
            expires_at = _expiry(created_at, self._expiry_hours)
            order = PaperOrder(
                order_id=_order_id(source_id),
                strategy_id="baseline_v1",
                source_type="baseline_v1",
                source_id=source_id,
                symbol=r["symbol"],
                direction=r["direction"],
                entry=entry,
                stop_loss=sl,
                tp1=tp1,
                tp2=tp2,
                rr=rr_val.quantize(Decimal("0.01")),
                status="OPEN",
                result=None,
                pushed=False,
                alert_id=None,
                created_at=created_at,
                filled_at=None,
                closed_at=None,
                expires_at=expires_at,
                pnl_pct=None,
                rr_realized=None,
                duration_minutes=None,
                legacy=False,
            )
            self._repo.save(order)
            count += 1
        return count
