"""Wrapper that starts the HTTP healthcheck server FIRST, then launches
the Binance AI Trader run-loop and the us-stock-hunter scheduler.

Startup sequence
----------------
1. Clear stale lock file (fcntl lock is released by OS when old process dies,
   but the file itself lingers and confuses the next run).
2. Start the healthcheck server with retry (old process may still hold port
   8080 for a brief window; retry up to ~10 s).
3. Clone/start us-stock-hunter.
4. Launch the run-loop.  If it exits LOCKED (race condition), retry a few more
   times before giving up.
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

_LOCK_FILE = Path("data/market_data.db.runner.lock")
_HEALTH_PORT = 8080
_LOCKED_EXIT_CODE = 3


def _clear_stale_lock() -> None:
    """Remove the on-disk lock file left by a previous process.

    fcntl.flock releases automatically when the process exits, but the file
    remains.  Deleting it lets SingleInstanceLock create a fresh one.
    """
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _start_health_server_with_retry(port: int = _HEALTH_PORT, max_wait: int = 12) -> None:
    """Start the health server, retrying if the port is still held by the
    previous deployment process (up to *max_wait* seconds)."""
    from binance_ai_trader.runner.http_health_server import _started_lock, _started

    deadline = time.monotonic() + max_wait
    while True:
        # Check if port is free before attempting to start.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                port_free = True
            except OSError:
                port_free = False

        if port_free:
            from binance_ai_trader.runner.http_health_server import start_health_server
            start_health_server(port)
            return

        if time.monotonic() >= deadline:
            # Last resort: start anyway; http_health_server logs the warning.
            from binance_ai_trader.runner.http_health_server import start_health_server
            start_health_server(port)
            return

        print(f"Health port {port} busy, waiting for old process to exit…", flush=True)
        time.sleep(1)


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
    _clear_stale_lock()
    _start_health_server_with_retry()
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

    for _attempt in range(6):
        _rc = main(_args)
        if _rc != _LOCKED_EXIT_CODE:
            sys.exit(_rc)
        print(f"run-loop still locked, retry {_attempt + 1}/6 in 3s…", flush=True)
        _clear_stale_lock()
        time.sleep(3)

    sys.exit(_LOCKED_EXIT_CODE)
