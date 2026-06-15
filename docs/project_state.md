# Project State Manifest

## Current Commands

- Market data: `collect-history`, `scan`, `regime`, `sectors`, `capital`, `space`.
- Research: `backtest`, `evaluate`, `walk-forward`, `auto-research`.
- Strategy Lab: `strategies list`, `strategies compare`, `strategies rank`,
  `strategies sweep`, `strategies champion`.
- Hotlist: `hotlist watch`, `hotlist scan`, `hotlist review`, `hotlist-alert`,
  `hotlist-ai-review`, `hotlist-performance`.
- Operations: `ops status`, `ops daily`, `ops safety-audit`,
  `telegram hotlist-test`, `health`, `daily-report`, `paper-simulate`.
- Runner: `run-loop`; add `--enable-hotlist-alerts` to opt into the 15-minute
  research alert task.

## Strategy Configurations

- `baseline_v1.json` — immutable production baseline.
- `range_disabled_v1.json`
- `bear_short_space80_v1.json`
- `capital_60_80_space80_v1.json`
- `breakout_hunter_v1.json`

The non-baseline configurations are research variants and are not live strategies.

## Reports

Operational research reports include `champion_league.md`,
`hotlist_daily_summary.md`, `hotlist_top5_review.md`, `hotlist_performance.md`,
`ops_daily.md`, walk-forward reports, historical collection reports, and P0 data
quality/lineage reports under `reports/`.

## Hotlist Modules

- `service.py`: public-data watcher and entry plans.
- `watchlist.py` / `repository.py`: rolling observation pool and alert persistence.
- `alerts.py`: quality filtering, alert levels, and deduplication.
- `ai_review.py`: deterministic review scaffold.
- `performance.py` / `performance_repository.py`: outcome evaluation and statistics.
- `reporting.py`: Markdown research reports.
- `telegram.py`: formatting only; sending is controlled by CLI/runner operations.
- `models.py`: Hotlist data contracts.

## Runner Options

Important options are `--database`, `--config`, `--sectors-config`,
`--baseline-config`, `--poll-seconds`, `--lock-file`, `--once`,
`--enable-hotlist-alerts`, and historical collection interval options.
Hotlist alerts are disabled by default.

## Safety Rules

- Research only; no live trading.
- Public Binance market-data endpoints only.
- No Binance account, order, balance, or position endpoints.
- No Binance API keys.
- Telegram credentials come from environment variables/Replit Secrets.
- `baseline_v1` and production signal/scoring logic remain unchanged.
- Telegram delivery occurs only for an explicit test or enabled runner alert task.

## Implemented Feature Set

- Strategy Lab Phase 1 variants, comparison, ranking, sweep/export, and Champion League.
- Hotlist watcher, rolling watchlist, alert engine, runner integration, AI review,
  and performance tracking.
- Operational status bundle, daily operating report, Telegram test, safety audit,
  and Replit runbook.
