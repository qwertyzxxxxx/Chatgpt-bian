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
    from binance_ai_trader.v3.runner.tasks import build_reversal_tasks, build_v3_tasks, build_v66_tasks, build_v662_tasks, build_v663_tasks, build_v664_tasks, build_wave_long_tasks, build_wave_short_tasks, build_classic_tasks, build_k_tasks, build_sma120_tasks, build_rsd_tasks
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

    # ── Live Mirror engines ───────────────────────────────────────────────────
    # LIVE_TRADING_ENABLED is the global kill switch.
    # Per-strategy on/off and notional are controlled at runtime via DB
    # (V3RuntimeSettingsRepository / Telegram /livemode /setlive).
    #
    # To add a new live strategy: add ONE entry to _LIVE_ENGINE_CFG below.
    # To remove a strategy from live: delete its entry — no other code changes.
    # Each strategy's task builder gets: live_mirror=live_mirrors.get(STRATEGY_ID)
    # which returns None automatically if not in this dict → paper-only.
    #
    # _LIVE_ENGINE_CFG format:
    #   strategy_id: (display_tag, env_var_for_notional, default_notional_str)
    live_mirrors: dict = {}   # strategy_id → LiveMirrorEngine | filled below

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
                    V3_STRATEGY_ID, V66_STRATEGY_ID, V663_STRATEGY_ID,
                    LIVE_DEFAULTS, STRATEGY_ALIASES,
                    V3RuntimeSettingsRepository,
                )

                _LIVE_ENGINE_CFG: dict[str, tuple[str, str, str]] = {
                    V3_STRATEGY_ID:   ("V3",   "ORDER_NOTIONAL_USDT",           "1000"),
                    V663_STRATEGY_ID: ("V663", "V663_ORDER_NOTIONAL_USDT",      "1000"),
                    "classic_c3":     ("C3",   "C3_ORDER_NOTIONAL_USDT",        "1000"),
                    "wave_long":      ("W↑",   "WAVE_LONG_ORDER_NOTIONAL_USDT", "1000"),
                    # ← add new live strategies here (one line each)
                }
                # 方向過濾：只在指定方向開實盤
                _LIVE_DIRECTION_FILTER: dict[str, set[str]] = {
                    V663_STRATEGY_ID: {"SHORT"},        # V663 實盤只做空
                    "classic_c3":     {"SHORT"},        # C3 反彈空
                    "wave_long":      {"LONG"},         # Wave↑ 放量突破做多
                }

                _live_client = BinanceFuturesClient(_api_key, _api_secret)
                _live_repo   = LiveOrderRepository()
                _max_pending = int(os.environ.get("MAX_PENDING_ORDERS", "10"))
                _max_pos     = int(os.environ.get("MAX_OPEN_POSITIONS", "5"))

                for _sid, (_tag, _env, _default) in _LIVE_ENGINE_CFG.items():
                    live_mirrors[_sid] = LiveMirrorEngine(
                        _live_client, _live_repo, notifier,
                        notional_usdt=_Dec(os.environ.get(_env, _default)),
                        max_pending=_max_pending,
                        max_positions=_max_pos,
                        strategy_id=_sid,
                        tag=_tag,
                        allowed_directions=_LIVE_DIRECTION_FILTER.get(_sid),
                    )

                _log.info(
                    "[startup] Live Mirror engines: %s  max_pending=%d max_pos=%d",
                    list(_LIVE_ENGINE_CFG.keys()), _max_pending, _max_pos,
                )
                if notifier:
                    try:
                        _sr = V3RuntimeSettingsRepository()
                        _id_to_alias = {v: k for k, v in STRATEGY_ALIASES.items()}
                        _lines = ["[LIVE] 实盘模块启动", "━━━━━━━━━━━━━━"]
                        for _sid2, _engine in live_mirrors.items():
                            _alias = _id_to_alias.get(_sid2, _sid2)
                            _on, _amt = _sr.resolve_live(_sid2)
                            _lines.append(f"{_alias.upper()}  {'ON' if _on else 'OFF'}  {_amt} USDT")
                        for _sid3 in LIVE_DEFAULTS:
                            if _sid3 not in live_mirrors:
                                _alias3 = _id_to_alias.get(_sid3, _sid3)
                                _lines.append(f"{_alias3.upper()}  -- 模拟盘 (无实盘引擎)")
                        _lines += [
                            f"最大挂单  {_max_pending}",
                            f"最大持仓  {_max_pos}",
                            "使用 /livestatus /livemode /setlive 调整",
                        ]
                        notifier.send("\n".join(_lines))
                    except Exception as _exc:
                        _log.warning("[startup] startup notify failed: %s", _exc)

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
        live_mirror=live_mirrors.get("hotlist_momentum_v3"),
        live_sync_interval=timedelta(minutes=int(os.environ.get("LIVE_SYNC_INTERVAL_MIN", "3"))),
        live_report_interval=timedelta(minutes=int(os.environ.get("LIVE_REPORT_INTERVAL_MIN", "60"))),
    ))

    # ── Build V66 tasks (V1-style watchlist, paper-only) ──────────────────────
    v66_tasks = build_v66_tasks(
        db_path=_DB_PATH,
        universe_config=universe_config,
        telegram=notifier,
        scan_interval=timedelta(minutes=15),
        settle_interval=timedelta(minutes=15),
        report_interval=timedelta(hours=1),
        dedup_hours=24,
        max_open_orders=5,
        live_mirror=live_mirrors.get("hotlist_v66"),
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

    # ── Build V663 tasks (EMA三线排列升级版，paper-only) ─────────────────────
    _v663_enabled = os.environ.get("ENABLE_V663", "").lower() == "true"
    if _v663_enabled:
        v663_tasks = build_v663_tasks(
            db_path=_DB_PATH,
            universe_config=universe_config,
            telegram=notifier,
            scan_interval=timedelta(minutes=15),
            settle_interval=timedelta(minutes=15),
            report_interval=timedelta(hours=1),
            dedup_hours=24,
            max_open_orders=5,
            live_mirror=live_mirrors.get("hotlist_v663"),
            live_sync_interval=timedelta(minutes=int(os.environ.get("LIVE_SYNC_INTERVAL_MIN", "3"))),
            live_report_interval=timedelta(minutes=int(os.environ.get("LIVE_REPORT_INTERVAL_MIN", "60"))),
        )
        tasks.extend(v663_tasks)
        _log.info("[startup] V663 enabled — live mirror wired (EMA三线排列升级版，实盘测试2000U)")

    # ── Build V664 tasks (精准回踩+量缩，多空双向，paper-only) ──────────────
    _v664_enabled = os.environ.get("ENABLE_V664", "").lower() == "true"
    if _v664_enabled:
        v664_tasks = build_v664_tasks(
            db_path=_DB_PATH,
            universe_config=universe_config,
            telegram=notifier,
            scan_interval=timedelta(minutes=15),
            settle_interval=timedelta(minutes=15),
            report_interval=timedelta(hours=1),
            dedup_hours=24,
            max_open_orders=5,
        )
        tasks.extend(v664_tasks)
        _log.info("[startup] V664 enabled — paper-only (精准回踩+量缩，多空双向)")

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

    # ── Wave Long Breakout tasks (放量突破回踩做多, paper-only) ───────────────
    _wave_long_enabled = os.environ.get("ENABLE_WAVE_LONG", "").lower() == "true"
    if _wave_long_enabled:
        wave_long_tasks = build_wave_long_tasks(
            db_path=_DB_PATH,
            universe_config=universe_config,
            telegram=notifier,
            scan_interval=timedelta(minutes=15),
            settle_interval=timedelta(minutes=15),
            report_interval=timedelta(hours=1),
            dedup_hours=24,
            max_open_orders=5,
            live_mirror=live_mirrors.get("wave_long"),
        )
        tasks.extend(wave_long_tasks)
        _wl_live = live_mirrors.get("wave_long")
        _log.info("[startup] wave_long enabled — %s (放量突破回踩做多)",
                  "live+paper" if _wl_live else "paper-only")

    # ── Wave Short Breakdown tasks (放量跌破反抽做空, paper-only) ─────────────
    _wave_short_enabled = os.environ.get("ENABLE_WAVE_SHORT", "").lower() == "true"
    if _wave_short_enabled:
        wave_short_tasks = build_wave_short_tasks(
            db_path=_DB_PATH,
            universe_config=universe_config,
            telegram=notifier,
            scan_interval=timedelta(minutes=15),
            settle_interval=timedelta(minutes=15),
            report_interval=timedelta(hours=1),
            dedup_hours=24,
            max_open_orders=5,
        )
        tasks.extend(wave_short_tasks)
        _log.info("[startup] wave_short enabled — paper-only (放量跌破反抽做空)")

    # ── RSD RSI Divergence tasks (rsd_long + rsd_short, paper-only) ──────────
    rsd_tasks = build_rsd_tasks(
        db_path=_DB_PATH,
        telegram=notifier,
        scan_interval=timedelta(minutes=15),
        settle_interval=timedelta(minutes=15),
        report_interval=timedelta(hours=1),
        dedup_hours=24,
        max_open_orders=5,
    )
    tasks.extend(rsd_tasks)
    _log.info("[startup] RSD — paper-only (RSI背離策略 rsd_long + rsd_short)")

    # ── Classic C1-C4 tasks (经典量价策略，paper-only) ──────────────────────
    _classic_enabled = os.environ.get("ENABLE_CLASSIC", "").lower() == "true"
    if _classic_enabled:
        from binance_ai_trader.classic.strategies.c1 import STRATEGY_ID as _C1_ID
        from binance_ai_trader.classic.strategies.c2 import STRATEGY_ID as _C2_ID
        from binance_ai_trader.classic.strategies.c3 import STRATEGY_ID as _C3_ID
        from binance_ai_trader.classic.strategies.c4 import STRATEGY_ID_TOP as _C4T_ID, STRATEGY_ID_BOT as _C4B_ID
        _ALL_CLASSIC = frozenset([_C1_ID, _C2_ID, _C3_ID, _C4T_ID, _C4B_ID])
        _classic_disabled = {
            _C1_ID if os.environ.get("DISABLE_CLASSIC_C1", "").lower() == "true" else None,
            _C2_ID if os.environ.get("DISABLE_CLASSIC_C2", "").lower() == "true" else None,
            _C3_ID if os.environ.get("DISABLE_CLASSIC_C3", "").lower() == "true" else None,
            _C4T_ID if os.environ.get("DISABLE_CLASSIC_C4", "").lower() == "true" else None,
            _C4B_ID if os.environ.get("DISABLE_CLASSIC_C4", "").lower() == "true" else None,
        } - {None}
        _classic_enabled_strategies = _ALL_CLASSIC - _classic_disabled if _classic_disabled else None
        classic_tasks = build_classic_tasks(
            db_path=_DB_PATH,
            telegram=notifier,
            scan_interval=timedelta(minutes=15),
            settle_interval=timedelta(minutes=15),
            report_interval=timedelta(hours=1),
            enabled_strategies=_classic_enabled_strategies,
            live_mirrors={k: v for k, v in live_mirrors.items() if k.startswith("classic_")},
        )
        tasks.extend(classic_tasks)
        _disabled_str = f" (disabled: {sorted(_classic_disabled)})" if _classic_disabled else ""
        _log.info("[startup] Classic C1-C4 enabled — paper-only (经典量价策略)%s", _disabled_str)

    # ── Classic K1-K4 — 新型量价策略，纸盘 ─────────────────────────────────────
    if os.environ.get("ENABLE_CLASSIC_K", "").lower() == "true":
        k_tasks = build_k_tasks(
            db_path=_DB_PATH,
            telegram=notifier,
            scan_interval=timedelta(minutes=15),
            settle_interval=timedelta(minutes=15),
            report_interval=timedelta(hours=1),
        )
        tasks.extend(k_tasks)
        _log.info("[startup] Classic K1-K4 enabled — paper-only (新型量价策略)")
    else:
        _log.info("[startup] Classic K1-K4 disabled (set ENABLE_CLASSIC_K=true to enable)")

    # ── SMA120 V1.9-D — XAUUSDT 模拟盘 ──────────────────────────────────────────
    # Always started; on/off controlled at runtime via Telegram /paperon sma120 /
    # /paperoff sma120 (stored in v3_runtime_settings, no redeploy needed).
    sma120_tasks = build_sma120_tasks(
        telegram=notifier,
        scan_interval=timedelta(minutes=5),
        settle_interval=timedelta(minutes=5),
        report_interval=timedelta(hours=1),
    )
    tasks.extend(sma120_tasks)
    _log.info("[startup] SMA120 V1.9-D loaded — XAUUSDT paper-only (模拟盘, runtime-toggleable)")

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
