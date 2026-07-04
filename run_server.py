"""V3-only production runner — PostgreSQL final architecture.

V1 / V2 完全停止。只运行 V3 Hotlist Pipeline。
SQLite: klines/universe cache only.
PostgreSQL: all permanent trading data.

Startup sequence
----------------
1. Clear stale lock file.
2. Start HTTP health server immediately.
3. Init PostgreSQL schema (idempotent).
4. Run SQLite → PostgreSQL migration (idempotent, ON CONFLICT DO NOTHING).
5. Drop V2 SQLite tables (one-time cleanup).
6. Build V3 tasks.
7. Send [V3] Started Telegram message.
8. Run forever.
"""
import logging
import os
import sqlite3
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)

_log = logging.getLogger(__name__)

_LOCK_FILE = Path("data/market_data.db.runner.lock")
_DB_PATH   = Path("data/market_data.db")
_CONFIG    = Path("config/universe.json")
_LOCKED_RC = 3


def _clear_stale_lock() -> None:
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Health server starts FIRST ────────────────────────────────────────────────
_clear_stale_lock()
from binance_ai_trader.runner.http_health_server import start_health_server
start_health_server()
# ─────────────────────────────────────────────────────────────────────────────


def _drop_v2_tables() -> None:
    if not _DB_PATH.exists():
        return
    try:
        con = sqlite3.connect(str(_DB_PATH))
        for tbl in ("v2_signals", "v2_paper_orders", "v2_order_events"):
            con.execute(f"DROP TABLE IF EXISTS {tbl}")
        con.commit()
        con.close()
        _log.info("[startup] V2 SQLite tables dropped")
    except Exception as exc:
        _log.warning("[startup] V2 table cleanup failed: %s", exc)


if __name__ == "__main__":
    from binance_ai_trader.config import UniverseConfig
    from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
    from binance_ai_trader.notifications.telegram import TelegramNotifier
    from binance_ai_trader.runner.engine import ProductionRunner, RunnerLockError
    from binance_ai_trader.v3.runner.tasks import build_v3_tasks
    from binance_ai_trader.v3.storage.migration import run_migration
    from binance_ai_trader.v3.storage.pg import init_schema
    from binance_ai_trader.v3.telegram.startup import send_v3_startup

    # ── Telegram ──────────────────────────────────────────────────────────────
    _token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    _chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    notifier = None
    if _token and _chat_id:
        try:
            notifier = TelegramNotifier(_token, _chat_id)
        except Exception as exc:
            _log.warning("[startup] Telegram init failed: %s", exc)

    # ── PostgreSQL: init schema ───────────────────────────────────────────────
    try:
        init_schema()
        _log.info("[startup] PostgreSQL schema ready")
    except Exception as exc:
        _log.error("[startup] PostgreSQL schema init failed: %s", exc)
        raise SystemExit(1) from exc

    # ── SQLite → PostgreSQL migration (idempotent) ────────────────────────────
    try:
        report = run_migration(_DB_PATH)
        if report.sqlite_total > 0:
            _log.info("[startup] migration complete — %d rows migrated", report.sqlite_total)
            if notifier:
                try:
                    notifier.send(
                        f"[V3] PostgreSQL Migration Completed\n"
                        f"{'━' * 26}\n"
                        f"SQLite total  {report.sqlite_total} rows\n"
                        f"PG total      {report.pg_total} rows\n"
                        f"耗时          {report.elapsed_seconds:.1f}s\n"
                        f"校验          {'✓ 一致' if report.success else '✗ 不一致'}\n"
                        + "\n".join(
                            f"  {r.table}: {r.sqlite_rows}→PG({r.pg_rows_after})"
                            for r in report.tables if r.sqlite_rows > 0
                        )
                    )
                except Exception:
                    pass
    except Exception as exc:
        _log.warning("[startup] migration failed (non-fatal): %s", exc)

    # ── One-time V2 SQLite cleanup ────────────────────────────────────────────
    _drop_v2_tables()

    # ── Build V3 tasks ────────────────────────────────────────────────────────
    universe_config = UniverseConfig.load(_CONFIG)
    tasks = build_v3_tasks(
        db_path=_DB_PATH,
        universe_config=universe_config,
        telegram=notifier,
        scan_interval=timedelta(minutes=15),
        settle_interval=timedelta(minutes=15),
        report_interval=timedelta(hours=1),
        health_interval=timedelta(hours=6),
        weekly_review_interval=timedelta(days=7),
        shadow_report_enabled=True,
        health_check_enabled=True,
        dedup_hours=24,
        max_open_orders=10,
    )
    _log.info("[startup] V3 tasks: %s", [t.event_type for t in tasks])

    # ── Telegram: [V3] Started ────────────────────────────────────────────────
    if notifier is not None:
        try:
            send_v3_startup(
                notifier,
                strategy_id="hotlist_momentum_v3",
                db_path="PostgreSQL",
                scan_interval_minutes=15,
                settle_interval_minutes=15,
                report_interval_hours=1,
                health_interval_hours=6,
                shadow_report_enabled=True,
                health_check_enabled=True,
            )
        except Exception as exc:
            _log.warning("[startup] send_v3_startup failed: %s", exc)

    # ── Run forever with lock-retry ───────────────────────────────────────────
    repository = MarketDataRepository(_DB_PATH)
    try:
        for _attempt in range(6):
            try:
                runner = ProductionRunner(
                    repository,
                    tasks,
                    _LOCK_FILE,
                    poll_seconds=30.0,
                )
                runner.run_forever()
                break
            except RunnerLockError:
                print(f"run-loop locked, clearing and retrying {_attempt + 1}/6…", flush=True)
                _clear_stale_lock()
                time.sleep(3)
            except KeyboardInterrupt:
                break
    finally:
        repository.close()
