"""Wrapper that starts the HTTP healthcheck server FIRST, then launches
the Binance AI Trader run-loop and the us-stock-hunter scheduler.

Startup sequence
----------------
1. Clear stale lock file so a restarting process can always acquire it.
2. Start the healthcheck server immediately (http_health_server handles port
   conflicts with a warning and does not crash).
3. Clone/start us-stock-hunter.
4. Launch the run-loop.  If it exits LOCKED (race), delete the lock file and
   retry up to 5 more times before giving up.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

_LOCK_FILE = Path("data/market_data.db.runner.lock")
_LOCKED_EXIT_CODE = 3


def _clear_stale_lock() -> None:
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Start health server immediately ──────────────────────────────────────────
_clear_stale_lock()
from binance_ai_trader.runner.http_health_server import start_health_server
start_health_server()
# ─────────────────────────────────────────────────────────────────────────────

_US_STOCK_HUNTER_DIR = Path("/home/runner/us-stock-hunter")
_US_STOCK_HUNTER_REPO = "https://github.com/qwertyzxxxxx/us-stock-hunter.git"


def _ensure_stock_hunter() -> bool:
    main_py = _US_STOCK_HUNTER_DIR / "main.py"
    if main_py.exists():
        return True
    try:
        _US_STOCK_HUNTER_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth=1", _US_STOCK_HUNTER_REPO,
             str(_US_STOCK_HUNTER_DIR)],
            check=True, timeout=120,
        )
    except Exception:
        return False
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r",
         str(_US_STOCK_HUNTER_DIR / "requirements.txt"), "-q",
         "--break-system-packages"],
        timeout=120,
    )
    return main_py.exists()


def _start_stock_hunter() -> None:
    if not _ensure_stock_hunter():
        return
    subprocess.Popen(
        [sys.executable, "main.py", "schedule"],
        cwd=str(_US_STOCK_HUNTER_DIR),
    )


if __name__ == "__main__":
    _start_stock_hunter()

    from binance_ai_trader.entrypoints.cli import main
    _args = [
        "run-loop",
        "--enable-hotlist-alerts",
        "--enable-hotlist-performance",
        "--enable-performance-center",
        "--enable-leaderboard-watch",
        "--leaderboard-gemini-max-candidates", "3",
        "--enable-strategy-health",
        "--enable-v2-hotlist",
        "--history-days", "30",
    ] + sys.argv[1:]

    for _attempt in range(6):
        _rc = main(_args)
        if _rc != _LOCKED_EXIT_CODE:
            sys.exit(_rc)
        print(f"run-loop locked, clearing and retrying {_attempt + 1}/6…", flush=True)
        _clear_stale_lock()
        time.sleep(3)

    sys.exit(_LOCKED_EXIT_CODE)
