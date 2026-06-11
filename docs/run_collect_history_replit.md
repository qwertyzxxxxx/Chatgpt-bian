# Run Historical Data Collection on Replit Reserved VM or a VPS

## Purpose and safety boundary

This runbook explains how to build or resume `data/market_data.db` outside the Codex execution
environment. Codex's outbound proxy returned HTTP 403 before Binance collection began; Replit
Reserved VM or a VPS with normal access to Binance public USD-M Futures endpoints is therefore
the recommended collection environment.

The collector is **read-only**:

- no Binance API key or secret is required;
- no account, balance, position, leverage, order, or execution endpoint is called;
- no live or paper order is submitted;
- no strategy, scoring, ranking, entry, stop-loss, or take-profit rule is changed;
- the database contains public market and research data, not exchange credentials.

The collector calls only public market-data endpoints for exchange metadata, 24-hour tickers,
klines, Open Interest history, Funding Rate history, and global Long/Short Ratio history.

## 1. Prepare Replit Reserved VM

### Import and storage

1. Import the GitHub repository into Replit.
2. Select Python 3.11 or newer.
3. Use a Reserved VM with persistent filesystem storage. Do not use an ephemeral workspace for
   the only copy of the database.
4. Open **Shell** and confirm that it starts in the repository root—the directory containing
   `pyproject.toml`, `src/`, `config/`, and `data/`.

```bash
pwd
python --version
find . -maxdepth 1 -type f -o -type d | sort
```

Create the data and backup directories if they do not already exist:

```bash
mkdir -p data backups reports
```

### VPS equivalent

On Ubuntu/Debian, install the prerequisites and clone the repository:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv sqlite3

git clone https://github.com/qwertyzxxxxx/Chatgpt-bian.git
cd Chatgpt-bian
python3 -m venv .venv
source .venv/bin/activate
```

If Binance is unavailable in the VPS region, use a permitted region/provider. Do not add an API
key as a workaround for a network, regional, or HTTP 403 restriction.

## 2. Install dependencies

The project currently uses the Python standard library and declares no third-party runtime
dependencies, but installing it in an isolated virtual environment verifies packaging and makes
the `binance-ai-trader` console command available.

### Replit Shell

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools
python -m pip install -e .
```

If Replit already manages the environment and refuses to create a virtual environment, use:

```bash
python -m pip install -e .
```

Verify the installation and CLI contract:

```bash
PYTHONPATH=src python -m binance_ai_trader --help
PYTHONPATH=src python -m binance_ai_trader collect-history --help
python -m compileall -q src tests
PYTHONPATH=src python -m unittest discover -s tests -v
```

The automated tests use fixtures and do not require Binance credentials.

## 3. Verify public Binance connectivity

Before starting a long job, test the exact public host used by the collector:

```bash
python - <<'PY'
import json
import urllib.request

url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
with urllib.request.urlopen(url, timeout=20) as response:
    payload = json.load(response)
print({"status": "OK", "symbols": len(payload.get("symbols", [])), "url": url})
PY
```

A successful response should print `status: OK` and a non-zero symbol count. If this check returns
HTTP 403, 451, a proxy error, or a timeout, resolve network/provider access before running the
collector. An API key does not fix public-host connectivity.

## 4. Start the 180-day collection

Run this command from the repository root:

```bash
PYTHONPATH=src python -m binance_ai_trader collect-history \
  --days 180 \
  --database data/market_data.db
```

For a long interactive session, use `tmux` on a VPS or run it as the foreground command of a
Replit Reserved VM deployment. To preserve a timestamped log while still seeing output:

```bash
set -o pipefail
PYTHONPATH=src python -m binance_ai_trader collect-history \
  --days 180 \
  --database data/market_data.db \
  --log-level INFO 2>&1 | tee "data/collect-history-$(date -u +%Y%m%dT%H%M%SZ).log"
```

With `pipefail`, the shell retains a non-zero collector exit status even though output is piped
through `tee`.

Optional resilience controls for a slow or rate-limited connection are:

```bash
PYTHONPATH=src python -m binance_ai_trader collect-history \
  --days 180 \
  --database data/market_data.db \
  --timeout 30 \
  --max-retries 8 \
  --request-pause 0.20 \
  --log-level INFO
```

Do not run two collectors against the same SQLite file simultaneously. Let one process finish,
then rerun it if required.

## 5. Resume after interruption or partial failure

Use the **same command, day range, configuration, and database path**:

```bash
PYTHONPATH=src python -m binance_ai_trader collect-history \
  --days 180 \
  --database data/market_data.db
```

Resume is safe because:

- klines are keyed by `(symbol, interval, open_time_ms)` and are upserted;
- Capital Flow observations are keyed by `(symbol, metric, observed_at_ms)` and deduplicated;
- completed daily universe snapshots are skipped;
- committed data remains in SQLite when another symbol or endpoint fails;
- rerunning repairs interrupted pages without duplicating logical observations.

Exit status meanings:

- `0`: collection completed without recorded series failures;
- `2`: partial completion; inspect the JSON `failures` field and rerun;
- another non-zero status or traceback: startup/network/database failure; preserve the database,
  fix the cause, and run the same command again.

Do not delete `data/market_data.db` merely because one attempt failed. Back it up first if a
manual repair is ever necessary.

## 6. Audit the database after collection

### Quick file and integrity checks

```bash
ls -lh data/market_data.db*
python - <<'PY'
import sqlite3

path = "data/market_data.db"
connection = sqlite3.connect(path)
try:
    print("integrity_check:", connection.execute("PRAGMA integrity_check").fetchone()[0])
    print("journal_mode:", connection.execute("PRAGMA journal_mode").fetchone()[0])
finally:
    connection.close()
PY
```

`integrity_check` must report `ok`.

### Collection readiness audit

Run this standard-library audit script from the repository root:

```bash
python - <<'PY'
import json
import sqlite3
from datetime import datetime, timezone

DB = "data/market_data.db"
connection = sqlite3.connect(DB)
connection.row_factory = sqlite3.Row

def scalar(sql, params=()):
    row = connection.execute(sql, params).fetchone()
    return row[0] if row else None

def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms is not None else None

try:
    interval_rows = {
        interval: scalar("SELECT COUNT(*) FROM klines WHERE interval=?", (interval,))
        for interval in ("15m", "1h", "4h")
    }
    interval_symbols = {
        interval: scalar("SELECT COUNT(DISTINCT symbol) FROM klines WHERE interval=?", (interval,))
        for interval in ("15m", "1h", "4h")
    }
    capital_by_metric = {
        row["metric"]: row["count"]
        for row in connection.execute(
            "SELECT metric, COUNT(*) AS count FROM capital_flow_observations GROUP BY metric"
        )
    }
    run_statuses = {
        row["status"]: row["count"]
        for row in connection.execute(
            "SELECT status, COUNT(*) AS count FROM collection_runs GROUP BY status"
        )
    }
    payload = {
        "distinct_symbols": scalar("SELECT COUNT(DISTINCT symbol) FROM klines"),
        "earliest_kline_utc": iso(scalar("SELECT MIN(open_time_ms) FROM klines")),
        "latest_kline_utc": iso(scalar("SELECT MAX(close_time_ms) FROM klines")),
        "kline_rows": interval_rows,
        "symbols_by_interval": interval_symbols,
        "universe_snapshot_rows": scalar("SELECT COUNT(*) FROM universe_snapshots"),
        "universe_snapshot_days": scalar("SELECT COUNT(DISTINCT run_id) FROM universe_snapshots"),
        "capital_observations": scalar("SELECT COUNT(*) FROM capital_flow_observations"),
        "capital_by_metric": capital_by_metric,
        "collection_runs": run_statuses,
        "latest_failures": [dict(row) for row in connection.execute(
            "SELECT id,status,data_quality_status,error_summary,started_at,finished_at "
            "FROM collection_runs WHERE status IN ('PARTIAL','FAILED') "
            "ORDER BY started_at DESC LIMIT 20"
        )],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
finally:
    connection.close()
PY
```

Review all of the following before declaring the bootstrap usable:

1. BTCUSDT and ETHUSDT are present in all three intervals.
2. Configured universe symbols have non-zero 15m, 1h, and 4h rows.
3. The earliest/latest timestamps cover the intended period.
4. Universe snapshots span the requested complete UTC days.
5. Capital observations include `OPEN_INTEREST`, `FUNDING_RATE`, `LONG_SHORT_RATIO`, and
   `QUOTE_VOLUME_24H`.
6. `PARTIAL` and `FAILED` runs are understood and repaired where possible.
7. Open Interest and Long/Short Ratio may have less than 180 days because Binance's public
   futures-data endpoints have limited historical retention; this must remain visible as a data
   quality limitation rather than being synthesized.

### Optional SQLite CLI queries

If `sqlite3` is installed:

```bash
sqlite3 -header -column data/market_data.db \
  "SELECT interval, COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols, MIN(open_time_ms) AS earliest_ms, MAX(close_time_ms) AS latest_ms FROM klines GROUP BY interval ORDER BY interval;"

sqlite3 -header -column data/market_data.db \
  "SELECT metric, COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols, MIN(observed_at_ms) AS earliest_ms, MAX(observed_at_ms) AS latest_ms FROM capital_flow_observations GROUP BY metric ORDER BY metric;"

sqlite3 -header -column data/market_data.db \
  "SELECT status, data_quality_status, COUNT(*) AS runs FROM collection_runs GROUP BY status, data_quality_status ORDER BY status;"
```

## 7. Back up or download the database

SQLite uses WAL mode, so obtain a consistent backup instead of copying only the main file while
a writer is active.

### Safest online backup

The Python backup API can create a consistent copy while the source database is open:

```bash
python - <<'PY'
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

source_path = Path("data/market_data.db")
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
target_path = Path("backups") / f"market_data-{stamp}.db"
target_path.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source_path) as source, sqlite3.connect(target_path) as target:
    source.backup(target)
print(target_path)
PY
```

Verify and optionally compress the generated backup:

```bash
python - <<'PY'
import glob
import sqlite3

path = sorted(glob.glob("backups/market_data-*.db"))[-1]
with sqlite3.connect(path) as connection:
    print(path, connection.execute("PRAGMA integrity_check").fetchone()[0])
PY

gzip -k backups/market_data-*.db
```

In Replit Files, open `backups/`, use the file menu on the timestamped `.db` or `.db.gz`, and
select **Download**.

### Stopped-process copy

If no collector, runner, or other writer is active, checkpoint WAL before copying:

```bash
python - <<'PY'
import sqlite3
with sqlite3.connect("data/market_data.db") as connection:
    print(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall())
PY

cp data/market_data.db "backups/market_data-$(date -u +%Y%m%dT%H%M%SZ).db"
```

Keep at least one backup outside Replit/VPS storage. The database is ignored by Git and should
not be committed to the source repository.

## 8. Run walk-forward validation after enough data exists

The default walk-forward policy requires, per fold:

- 720 training points;
- 96 embargo points;
- 240 validation points;
- another 96 embargo points;
- 240 test points.

That is at least **1,392 eligible timestamps** for the first full fold. Raw 15-minute row count
alone is not proof of readiness: eligible timestamps must also have the required multi-timeframe
history and point-in-time inputs used by the analysis/backtest path.

First confirm the strategy is registered:

```bash
PYTHONPATH=src python -m binance_ai_trader strategies list \
  --database data/market_data.db \
  --baseline-config config/strategies/baseline_v1.json
```

Then run the baseline walk-forward validation:

```bash
PYTHONPATH=src python -m binance_ai_trader walk-forward \
  baseline_v1 \
  --database data/market_data.db \
  --sectors-config config/sectors.json \
  --baseline-config config/strategies/baseline_v1.json \
  --train-points 720 \
  --validation-points 240 \
  --test-points 240 \
  --step-points 240 \
  --embargo-points 96 \
  --point-stride 1 \
  --report reports/walk_forward_validation.md
```

The command writes train, validation, and out-of-sample test metrics to
`reports/walk_forward_validation.md`. Treat zero-fold output as a data-readiness failure, not as
strategy evidence. Do not reduce windows merely to force a result without documenting the loss
of statistical usefulness.

## 9. Expected runtime and resource use

Runtime depends primarily on the number of current/configured symbols, Binance response latency,
rate limiting, retries, and Replit/VPS CPU and network performance. A 180-day bootstrap can take
**tens of minutes to several hours**. The collector paginates three kline intervals and multiple
Capital Flow series per symbol, so a large universe is expected to be a long-running task.

Operational expectations:

- leave several gigabytes of free disk space for the database, WAL file, logs, and backups;
- keep the process attached to a Reserved VM deployment or `tmux`/`systemd` on a VPS;
- do not interrupt it merely because one symbol takes several minutes;
- watch the log for retries and the final JSON summary;
- use `--request-pause 0.20` or higher if Binance responds with rate-limit errors.

## 10. Common errors

### HTTP 403 or 451

Cause: proxy, hosting-provider, or regional access restriction.

Action: verify the connectivity command above, use an environment/region where public Binance
USD-M endpoints are permitted, and rerun. Do not add API credentials.

### HTTP 429 or rate-limit responses

Cause: requests are arriving too quickly.

Action: rerun with a larger pause and retries:

```bash
PYTHONPATH=src python -m binance_ai_trader collect-history \
  --days 180 --database data/market_data.db \
  --request-pause 0.50 --max-retries 10 --timeout 30
```

### Timeout, connection reset, or temporary 5xx response

Action: keep the existing database and rerun with a longer timeout/retry count. Idempotent writes
preserve completed pages.

### Exit code 2 or `PARTIAL` result

Action: inspect `failures` in the final JSON and `collection_runs.error_summary`, then rerun the
same command. Already stored rows remain valid.

### `database is locked`

Cause: two collectors, a collector plus maintenance command, or another long write transaction.

Action: stop the duplicate writer, wait briefly, and rerun. Never run two historical collectors
against the same database.

### Disk full

Action: stop collection, download a backup, remove obsolete logs/backups only after verifying an
off-machine copy, free space, and resume. Do not delete database tables to make room.

### No walk-forward folds

Cause: insufficient eligible timestamps or incomplete point-in-time inputs even if raw klines
exist.

Action: audit interval/symbol coverage, repair partial collection, continue accumulating data,
and rerun walk-forward after the database meets the minimum window requirements.

## Final safety confirmation

`collect-history` downloads and stores public historical market data only. It requires **no API
key**, reads **no account information**, submits **no orders**, performs **no live trading**, and
does not enable a candidate strategy. Walk-forward validation is offline research and does not
execute trades or guarantee future returns.
