"""Wrapper that starts the HTTP healthcheck server FIRST, then launches
the Binance AI Trader run-loop and the us-stock-hunter scheduler.

The healthcheck must be listening before any blocking work (git clone, pip)
so that Replit's deployment probe does not time out.

On deployment restarts the old process may still hold the run-loop lock for a
few seconds. We retry up to 10 times (2-second sleep between attempts) so the
new process waits for the old one to release the lock instead of failing.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from binance_ai_trader.runner.http_health_server import start_health_server
start_health_server()

_US_STOCK_HUNTER_DIR = Path("/home/runner/us-stock-hunter")
_US_STOCK_HUNTER_REPO = "https://github.com/qwertyzxxxxx/us-stock-hunter.git"

_LOCKED_EXIT_CODE = 3
_LOCK_RETRY_WAIT = 2
_LOCK_MAX_RETRIES = 10


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
        "--enable-gemini-committee",
        "--enable-performance-center",
        "--enable-leaderboard-watch",
        "--enable-strategy-health",
    ] + sys.argv[1:]
    for _attempt in range(_LOCK_MAX_RETRIES):
        _rc = main(_args)
        if _rc != _LOCKED_EXIT_CODE:
            sys.exit(_rc)
        print(f"run-loop lock held by previous process, retrying in {_LOCK_RETRY_WAIT}s "
              f"(attempt {_attempt + 1}/{_LOCK_MAX_RETRIES})", flush=True)
        time.sleep(_LOCK_RETRY_WAIT)
    sys.exit(_LOCKED_EXIT_CODE)
