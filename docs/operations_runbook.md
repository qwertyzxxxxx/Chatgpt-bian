# Hotlist Trading Assistant Operations Runbook

The application is a research and monitoring system. It does not place orders, access
accounts, read balances, or manage positions.

## Configure Replit Secrets

Open **Replit → Tools → Secrets** and add:

- `TELEGRAM_BOT_TOKEN`: token issued by BotFather.
- `TELEGRAM_CHAT_ID`: destination chat, group, or channel ID.

Never commit either value to Git, source files, reports, or `.env` files. Restart the
Replit process after changing Secrets so the environment is refreshed.

## One-Time Checks

From the repository root:

```bash
PYTHONPATH=src python -m binance_ai_trader ops status
PYTHONPATH=src python -m binance_ai_trader ops safety-audit
PYTHONPATH=src python -m binance_ai_trader telegram hotlist-test
PYTHONPATH=src python -m binance_ai_trader hotlist review
PYTHONPATH=src python -m binance_ai_trader hotlist-alert
PYTHONPATH=src python -m binance_ai_trader hotlist-performance
PYTHONPATH=src python -m binance_ai_trader ops daily
```

`telegram hotlist-test` returns `SKIPPED` when either Telegram Secret is missing.

## Start the Runner

Hotlist alerts are opt-in:

```bash
PYTHONPATH=src python -m binance_ai_trader run-loop --enable-hotlist-alerts
```

The hotlist task runs every 15 minutes, uses public Binance market data, preserves
60-minute alert deduplication, and sends Telegram only when alerts exist.

## Check Reports

Review Markdown files in `reports/`, especially:

- `reports/hotlist_daily_summary.md`
- `reports/hotlist_top5_review.md`
- `reports/hotlist_performance.md`
- `reports/champion_league.md`
- `reports/ops_daily.md`

Refresh the operating report with:

```bash
PYTHONPATH=src python -m binance_ai_trader ops daily
```

## Stop the Runner

In an interactive Replit Shell, press **Ctrl+C**. For a Replit Reserved VM or
Deployment, use its **Stop** control. Do not start a second runner against the same
database; the lock file intentionally prevents duplicate processes.

## Common Errors

- **`SKIPPED: telegram_not_configured`**: configure both Telegram Secrets.
- **Runner lock error**: another runner is active. Stop it before restarting.
- **SQLite unhealthy**: stop writers, back up `data/market_data.db`, and inspect
  `ops status` or `health`.
- **No successful backtest**: Champion League remains unavailable until a backtest
  is persisted; `ops daily` reports that state without failing.
- **Binance timeout/rate limit**: wait and retry; only public endpoints are used.
- **No alerts**: this is normal when no opportunity passes the quality gates or
  deduplication window.

Every Telegram hotlist message and operational report is research-only.
