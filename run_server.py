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
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

_US_STOCK_HUNTER_DIR = Path(__file__).parent / "us-stock-hunter"


def _start_stock_hunter() -> None:
    if not _US_STOCK_HUNTER_DIR.exists():
        return
    subprocess.Popen(
        [sys.executable, "main.py", "schedule"],
        cwd=str(_US_STOCK_HUNTER_DIR),
    )
    _log_early = logging.getLogger(__name__)
    _log_early.info("[startup] us-stock-hunter scheduler started")

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
    _start_stock_hunter()

    from binance_ai_trader.config import UniverseConfig
    from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
    from binance_ai_trader.notifications.telegram import TelegramNotifier
    from binance_ai_trader.runner.engine import ProductionRunner, RunnerLockError
    from binance_ai_trader.v3.runner.tasks import build_reversal_tasks, build_v3_tasks, build_v66_tasks, build_v662_tasks
    from binance_ai_trader.v3.storage.migration import run_migration
    from binance_ai_trader.v3.storage.pg import init_schema
    from binance_ai_trader.v3.telegram.startup import send_v3_startup
    from binance_ai_trader.v3.telegram.command_server import start_command_server

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

    # ── Live Mirror (optional) ────────────────────────────────────────────────
    # V3 and V66 each get their OWN LiveMirrorEngine instance, scoped by
    # strategy_id, so their orders/notional/conflict-resolution never cross.
    # The env var LIVE_TRADING_ENABLED is a global kill switch: if it's not
    # "true", neither engine is even constructed. Per-strategy on/off and
    # position size are then controlled at runtime via DB settings
    # (V3RuntimeSettingsRepository / Telegram /livemode /setlive commands).
    live_mirror = None       # V3
    v66_live_mirror = None   # V66
    _live_enabled = os.environ.get("LIVE_TRADING_ENABLED", "").lower() == "true"
    if _live_enabled:
        _api_key    = os.environ.get("BINANCE_API_KEY", "")
        _api_secret = os.environ.get("BINANCE_API_SECRET", "")
        if _api_key and _api_secret:
            try:
                from decimal import Decimal as _Dec
                from binance_ai_trader.v3.live.client import BinanceFuturesClient
                from binance_ai_trader.v3.live.engine import LiveMirrorEngine
                from binance_ai_trader.v3.live.repository import LiveOrderRepository
                from binance_ai_trader.v3.settings.repository import (
                    V3_STRATEGY_ID, V66_STRATEGY_ID,
                )
                _live_client = BinanceFuturesClient(_api_key, _api_secret)
                _live_repo   = LiveOrderRepository()
                _max_pending = int(os.environ.get("MAX_PENDING_ORDERS", "10"))
                _max_pos     = int(os.environ.get("MAX_OPEN_POSITIONS", "5"))

                _v3_notional = _Dec(os.environ.get("ORDER_NOTIONAL_USDT", "1000"))
                live_mirror  = LiveMirrorEngine(
                    _live_client, _live_repo, notifier,
                    notional_usdt=_v3_notional,
                    max_pending=_max_pending,
                    max_positions=_max_pos,
                    strategy_id=V3_STRATEGY_ID,
                    tag="V3",
                )

                _v66_notional = _Dec(os.environ.get("V66_ORDER_NOTIONAL_USDT", "2000"))
                v66_live_mirror = LiveMirrorEngine(
                    _live_client, _live_repo, notifier,
                    notional_usdt=_v66_notional,
                    max_pending=_max_pending,
                    max_positions=_max_pos,
                    strategy_id=V66_STRATEGY_ID,
                    tag="V66",
                )

                _log.info(
                    "[startup] Live Mirror initialised — V3 notional=%sU (live_enabled db-controlled), "
                    "V66 notional=%sU (live_enabled db-controlled), max_pending=%d max_pos=%d",
                    _v3_notional, _v66_notional, _max_pending, _max_pos,
                )
                if notifier:
                    try:
                        from binance_ai_trader.v3.settings.repository import V3RuntimeSettingsRepository
                        _settings_repo = V3RuntimeSettingsRepository()
                        _v3_on, _v3_amt = _settings_repo.resolve_live(V3_STRATEGY_ID)
                        _v66_on, _v66_amt = _settings_repo.resolve_live(V66_STRATEGY_ID)
                    except Exception:
                        _v3_on, _v3_amt = False, _v3_notional
                        _v66_on, _v66_amt = True, _v66_notional
                    notifier.send(
                        "[LIVE] 实盘模块启动\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"V3  实盘 {'ON' if _v3_on else 'OFF'}  仓位 {_v3_amt} USDT\n"
                        f"V66 实盘 {'ON' if _v66_on else 'OFF'}  仓位 {_v66_amt} USDT\n"
                        f"最大挂单  {_max_pending}\n"
                        f"最大持仓  {_max_pos}\n"
                        f"使用 /livestatus /livemode /setlive 调整"
                    )
            except Exception as exc:
                _log.error("[startup] Live Mirror init failed: %s", exc)
        else:
            _log.warning("[startup] LIVE_TRADING_ENABLED=true but BINANCE_API_KEY/SECRET missing")

    # ── Build V3 tasks ────────────────────────────────────────────────────────
    universe_config = UniverseConfig.load(_CONFIG)
    tasks = list(build_v3_tasks(
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
        live_mirror=live_mirror,
        live_sync_interval=timedelta(minutes=int(os.environ.get("LIVE_SYNC_INTERVAL_MIN", "3"))),
        live_report_interval=timedelta(minutes=int(os.environ.get("LIVE_REPORT_INTERVAL_MIN", "60"))),
    ))

    # ── Build V66 tasks (V1-style watchlist, own live mirror) ─────────────────
    v66_tasks = build_v66_tasks(
        db_path=_DB_PATH,
        universe_config=universe_config,
        telegram=notifier,
        scan_interval=timedelta(minutes=15),
        settle_interval=timedelta(minutes=15),
        report_interval=timedelta(hours=1),
        dedup_hours=24,
        max_open_orders=5,
        live_mirror=v66_live_mirror,
        live_sync_interval=timedelta(minutes=int(os.environ.get("LIVE_SYNC_INTERVAL_MIN", "3"))),
        live_report_interval=timedelta(minutes=int(os.environ.get("LIVE_REPORT_INTERVAL_MIN", "60"))),
    )
    tasks.extend(v66_tasks)

    # ── Build V662 tasks (量比+趋势升级版，paper-only) ────────────────────────
    _v662_enabled = os.environ.get("ENABLE_V662", "").lower() == "true"
    if _v662_enabled:
        v662_tasks = build_v662_tasks(
            db_path=_DB_PATH,
            universe_config=universe_config,
            telegram=notifier,
            scan_interval=timedelta(minutes=15),
            settle_interval=timedelta(minutes=15),
            report_interval=timedelta(hours=1),
            dedup_hours=24,
            max_open_orders=5,
        )
        tasks.extend(v662_tasks)
        _log.info("[startup] V662 enabled — paper-only (量比+趋势升级版)")

    # ── Build hotlist_reversal tasks (V-Reversal, paper-only, no live mirror) ─
    _reversal_enabled = os.environ.get("ENABLE_HOTLIST_REVERSAL", "").lower() == "true"
    if _reversal_enabled:
        reversal_tasks = build_reversal_tasks(
            db_path=_DB_PATH,
            telegram=notifier,
            scan_interval=timedelta(minutes=15),
            settle_interval=timedelta(minutes=15),
            report_interval=timedelta(hours=1),
            dedup_hours=24,
            max_open_orders=5,
        )
        tasks.extend(reversal_tasks)
        _log.info("[startup] hotlist_reversal (V-Reversal) enabled — paper-only")

    _log.info("[startup] All tasks: %s", [t.event_type for t in tasks])

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

    # ── Telegram command server (interactive diagnostics) ─────────────────────
    start_command_server(notifier, _DB_PATH, universe_config)

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
