"""One-time migration: copy V3 SQLite data → PostgreSQL.

Run automatically on startup (idempotent — uses ON CONFLICT DO NOTHING).
Validates row counts after import and returns a MigrationReport.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from binance_ai_trader.v3.storage.pg import get_conn

log = logging.getLogger(__name__)

_V3_TABLES = (
    "v3_candidates",
    "v3_push_queue",
    "v3_paper_orders",
    "v3_order_events",
    "v3_feature_store",
)


@dataclass
class TableMigrationResult:
    table: str
    sqlite_rows: int
    pg_rows_before: int
    pg_rows_after: int
    inserted: int
    ok: bool


@dataclass
class MigrationReport:
    db_path: str
    elapsed_seconds: float
    tables: list[TableMigrationResult]
    sqlite_total: int
    pg_total: int
    success: bool

    def summary(self) -> str:
        lines = [
            f"[Migration] {self.db_path}  elapsed={self.elapsed_seconds:.1f}s",
        ]
        for t in self.tables:
            lines.append(
                f"  {t.table}: SQLite={t.sqlite_rows} → PG inserted={t.inserted} "
                f"(PG after={t.pg_rows_after}) {'✓' if t.ok else '✗'}"
            )
        lines.append(
            f"  TOTAL  SQLite={self.sqlite_total} PG={self.pg_total} "
            f"{'✓ OK' if self.success else '✗ MISMATCH'}"
        )
        return "\n".join(lines)


def _pg_count(cur, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    row = cur.fetchone()
    return row[0] if row else 0


def _sqlite_has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def run_migration(sqlite_path: Path) -> MigrationReport:
    """Migrate SQLite V3 tables → PostgreSQL. Idempotent."""
    t0 = time.monotonic()

    if not sqlite_path.exists():
        log.info("[migration] SQLite not found at %s — nothing to migrate", sqlite_path)
        return MigrationReport(
            db_path=str(sqlite_path),
            elapsed_seconds=0.0,
            tables=[],
            sqlite_total=0,
            pg_total=0,
            success=True,
        )

    sq_conn = sqlite3.connect(str(sqlite_path))
    sq_conn.row_factory = sqlite3.Row
    pg_conn = get_conn()

    results: list[TableMigrationResult] = []

    try:
        with pg_conn.cursor() as cur:
            for table in _V3_TABLES:
                if not _sqlite_has_table(sq_conn, table):
                    log.info("[migration] table %s not in SQLite — skip", table)
                    results.append(TableMigrationResult(
                        table=table, sqlite_rows=0,
                        pg_rows_before=_pg_count(cur, table),
                        pg_rows_after=_pg_count(cur, table),
                        inserted=0, ok=True,
                    ))
                    continue

                rows = sq_conn.execute(f"SELECT * FROM {table}").fetchall()
                sq_count = len(rows)
                pg_before = _pg_count(cur, table)

                if sq_count == 0:
                    results.append(TableMigrationResult(
                        table=table, sqlite_rows=0,
                        pg_rows_before=pg_before, pg_rows_after=pg_before,
                        inserted=0, ok=True,
                    ))
                    continue

                col_names = [d[0] for d in sq_conn.execute(
                    f"SELECT * FROM {table} LIMIT 0"
                ).description]
                placeholders = ",".join(["%s"] * len(col_names))
                cols_sql = ",".join(col_names)

                for row in rows:
                    values = tuple(row[c] for c in col_names)
                    try:
                        cur.execute(
                            f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders}) "
                            f"ON CONFLICT DO NOTHING",
                            values,
                        )
                    except Exception as exc:
                        log.warning("[migration] %s row skip: %s", table, exc)

                pg_conn.commit()
                pg_after = _pg_count(cur, table)
                inserted = pg_after - pg_before

                ok = pg_after >= sq_count
                results.append(TableMigrationResult(
                    table=table, sqlite_rows=sq_count,
                    pg_rows_before=pg_before, pg_rows_after=pg_after,
                    inserted=inserted, ok=ok,
                ))
                log.info(
                    "[migration] %s: sqlite=%d inserted=%d pg_total=%d",
                    table, sq_count, inserted, pg_after,
                )

    finally:
        sq_conn.close()
        pg_conn.close()

    sqlite_total = sum(r.sqlite_rows for r in results)
    pg_total     = sum(r.pg_rows_after for r in results)
    success      = all(r.ok for r in results)
    elapsed      = time.monotonic() - t0

    report = MigrationReport(
        db_path=str(sqlite_path),
        elapsed_seconds=elapsed,
        tables=results,
        sqlite_total=sqlite_total,
        pg_total=pg_total,
        success=success,
    )
    log.info("[migration] complete:\n%s", report.summary())
    return report
