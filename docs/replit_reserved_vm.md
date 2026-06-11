# Replit Reserved VM 24/7 Runner

This guide runs Binance AI Trader as a persistent **read-only research process** on a Replit Reserved VM. The runner uses Binance public market endpoints only. It has no API key, account access, order endpoint, live-trading capability, or web dashboard. Optional Telegram Bot API notifications are outbound-only.

## Before starting

1. Import the GitHub repository into Replit.
2. Select a Python environment (Python 3.11 or newer).
3. Open Replit Shell and run:

```bash
python -m compileall -q src tests
PYTHONPATH=src python -m unittest discover -s tests -v
```

The default SQLite database is `data/market_data.db`. Reserved VM storage must be persistent if you want history to survive deployments.

## Start the loop

In the Reserved VM deployment command, use:

```bash
PYTHONPATH=src python -m binance_ai_trader run-loop \
  --database data/market_data.db \
  --config config/universe.json \
  --sectors-config config/sectors.json \
  --baseline-config config/strategies/baseline_v1.json \
  --log-level INFO
```

The process remains in the foreground so Replit can monitor and restart it. Its UTC schedule is:

| Task | Schedule |
|---|---|
| `scan` | Every 15 minutes |
| `evaluate` | Every 15 minutes |
| `paper-simulate` | Every 15 minutes |
| `daily-report` | Daily at 00:05 UTC |
| `collect-history` | Every 24 hours |
| `auto-research` | Every 6 hours |

Each execution is written to `runner_events`. A task exception or non-zero exit code is recorded as `FAILED`; later tasks and future ticks continue. `auto-research` remains parameter research only and cannot approve or activate candidates.

The history job uses the existing resumable collector with a 180-day window. Operational cadence can be changed with `--history-interval-hours`, and the window with `--history-days`; neither option changes signal, scoring, or strategy behavior.

### Optional Telegram notifications

Set both deployment environment variables (never commit the real token):

```bash
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"
```

The runner sends failed-task alerts and the successful 00:05 UTC daily Top3 report. Telegram delivery errors are logged and isolated from later runner tasks.

For a one-cycle operational check, use `--once`. This intentionally attempts all six tasks immediately:

```bash
PYTHONPATH=src python -m binance_ai_trader run-loop --once \
  --database data/market_data.db
```

## Single-instance protection

The runner obtains an operating-system file lock at `data/market_data.db.runner.lock` by default. A second process exits with code `3` and a JSON `LOCKED` error. The lock is released automatically when the process exits. Do not disable or delete the lock file while a runner process is active.

A custom path can be supplied with `--lock-file`:

```bash
PYTHONPATH=src python -m binance_ai_trader run-loop \
  --database data/market_data.db \
  --lock-file data/production.runner.lock
```

## Stop and restart

* In Replit Deployments, use **Stop** to send the process a termination signal.
* For an interactive Shell process, press **Ctrl+C**.
* Restart with the same command and database path. Schedule decisions use persisted `runner_events`, so a restart does not immediately duplicate a task that already ran inside its interval.

Never start a second `run-loop` against the same database. The lock rejects it, but a single deployment is easier to operate and audit.

## Logs and errors

View stdout/stderr in the Reserved VM deployment logs. Successful CLI task payloads are JSON; Python logging records task failures. Query current health from a second Shell:

```bash
PYTHONPATH=src python -m binance_ai_trader health \
  --database data/market_data.db
```

The health JSON includes the latest scan time, latest BTC/ETH regime, latest signal count, latest runner error, paper equity, database size, `aggressive_allowed`, and SQLite `quick_check`, foreign-key violation count, and journal mode. The command exits with code `2` when SQLite is unhealthy.

You can inspect recent runner events with SQLite if the `sqlite3` command is available:

```bash
sqlite3 data/market_data.db \
  "SELECT event_type,status,started_at,duration_ms,error_message FROM runner_events ORDER BY started_at DESC LIMIT 20;"
```

A Binance `403`, timeout, or proxy error should appear as a failed `scan` event. The process continues to evaluate stored data and will retry scan on a later tick. Do not add an API key as a workaround.

## Download or back up SQLite

1. Stop the deployment or wait until no task is writing.
2. In Replit Files, locate `data/market_data.db`.
3. Use the file menu to download it.
4. If `market_data.db-wal` exists, stop the runner first and run a checkpoint before downloading:

```bash
python - <<'PY'
import sqlite3
connection = sqlite3.connect("data/market_data.db")
connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
connection.close()
PY
```

Keep downloaded databases private if they contain research history you do not want to publish. They contain no Binance credentials because this project does not use any.

## If the database becomes large

Check its total size:

```bash
PYTHONPATH=src python -m binance_ai_trader health --database data/market_data.db
```

Then:

1. Download a backup.
2. Stop `run-loop`.
3. Checkpoint WAL using the command above.
4. Optionally run `VACUUM` when sufficient free disk space exists:

```bash
python - <<'PY'
import sqlite3
connection = sqlite3.connect("data/market_data.db")
connection.execute("VACUUM")
connection.close()
PY
```

Do not delete recent klines blindly: evaluation, backtest, sector research, and auto-research depend on historical data. If retention is required, define and test a separate archival policy before deleting rows.

## Daily operator checklist

Review these values at least once per day:

1. `health.last_scan_at` is recent.
2. `health.last_runner_error` is absent or understood.
3. The latest `combined_regime` is not stale.
4. Signal count and LONG/SHORT mix are plausible for the regime.
5. Paper equity, consecutive losses, pause state, and `aggressive_allowed`.
6. Daily-report Top 5 candidates have enough signals and positive validated metrics.
7. Database size remains within Reserved VM storage limits.

Run the report manually when needed:

```bash
PYTHONPATH=src python -m binance_ai_trader daily-report \
  --database data/market_data.db
```

Paper milestones are research labels, not return promises. The runner never places trades and does not guarantee profitability.
