# PROJECT_STATUS.md — market-hunter

Audit as of: 2026-06-18

---

## How to Run

```bash
# One-off scan (fetches universe, runs strategies, saves to DB, sends Telegram)
python3 main.py scan-us

# Evaluate historical signal performance (needs signals ≥ 5 trading days old)
python3 main.py evaluate-us

# Print performance report to terminal
python3 main.py report-us

# Start daily scheduler (blocks — runs scan at 06:30 MYT every day)
python3 main.py schedule
```

Workflows in the Replit panel:
- **market-hunter: Scan US** — runs `scan-us`
- **market-hunter: Scheduler** — runs `schedule`
- **market-hunter: Report** — runs `report-us`

No workflow exists yet for `evaluate-us` — run it from the Shell.

---

## Module Audit

### ✅ Completed

| File | Status | Notes |
|---|---|---|
| `market_hunter/config.py` | Complete | All thresholds, env var reads, scheduler config |
| `market_hunter/data/fmp.py` | Complete | FMP screener fetch, universe filtering |
| `market_hunter/data/price.py` | Complete | yfinance OHLCV, MA20/50/60/200, VolMA20, DollarVol, 52-week high |
| `market_hunter/strategies/ma60_reclaim.py` | Complete | Downtrend → MA60 cross → pullback logic |
| `market_hunter/strategies/strong_trend.py` | Complete | MA20>MA50>MA200, pullback near MA20/MA50, 52w distance |
| `market_hunter/strategies/new_high.py` | Complete | 52-week high break, 1.5× volume, price above MA20/MA50 |
| `market_hunter/scoring/scorer.py` | Complete | 5 sub-scores, 0–100 total |
| `market_hunter/telegram/notifier.py` | Complete | HTML-formatted report, error alerts, graceful no-op if unconfigured |
| `market_hunter/database/db.py` | Complete | 4 tables: signals, scan_runs, strategy_results, evaluations |
| `market_hunter/scheduler/runner.py` | Complete | APScheduler BlockingScheduler, CronTrigger, 06:30 MYT |
| `market_hunter/scanner.py` | Complete | Full orchestration: universe → price → filter → strategies → score → DB → Telegram |
| `market_hunter/evaluator.py` | Complete | 5/10/20-day returns, max drawdown, max gain, stores to evaluations table |
| `market_hunter/report.py` | Complete | CLI table: scan runs, evaluated signals, avg returns, win rate |
| `main.py` | Complete | CLI dispatcher for scan-us / evaluate-us / report-us / schedule |
| `README.md` | Complete | Replit + Linux VPS setup, systemd service, scoring table, strategy descriptions |
| `.gitignore` | Complete | Covers `__pycache__/`, `*.pyc`, `.env`, `*.db`, `logs/` |

---

### ⚠️ Partially Completed

**1. FMP universe filtering (ADR / preferred share exclusion)**
- `config.py` → `data/fmp.py`
- The FMP screener accepts `isEtf=false` which removes most ETFs. Warrants, units, and preferred shares are filtered by name keyword and symbol pattern heuristics.
- **Gap**: FMP does not expose a `securityType` field on the screener endpoint. ADRs that have plain ticker symbols (e.g. `TSM`, `ASML`) will pass through. True common-stock-only filtering would require a separate call to `/profile/{symbol}` for each ticker and checking the `isAdr` field — too slow for bulk scanning.
- **Risk**: Low-medium. Large-cap ADRs are legitimate trading instruments; most users won't mind. Filter is documented in code.

**2. Signal deduplication**
- `database/db.py`, `scanner.py`
- If `scan-us` is run more than once on the same calendar date, duplicate rows are inserted into `signals` and `strategy_results`.
- **Gap**: No `UNIQUE` constraint on `(symbol, signal_date)` and no pre-check in `insert_signal`.
- **Risk**: Medium if the scheduler misfires or the user runs the scan manually on the same day. The report will double-count signals.

**3. Logging to file**
- `main.py`
- Logging is configured to stdout only. The `.gitignore` excludes a `logs/` directory, but no `FileHandler` is set up in the project.
- **Gap**: On a VPS, stdout logging is lost on restart unless the process manager captures it. No `logs/` directory is created.
- **Risk**: Low for Replit (workflow panel shows stdout). Medium for VPS deployments.

**4. `evaluate-us` workflow missing from panel**
- Three workflows exist in the Replit panel: Scan US, Scheduler, Report.
- `evaluate-us` has no panel workflow — it must be run manually from the Shell.
- **Gap**: Minor convenience gap only.

---

### ❌ Missing

**1. `requirements.txt`**
- The README instructs VPS users to `pip install yfinance requests apscheduler pytz` manually.
- No `requirements.txt` file exists in the repo.
- **Impact**: VPS setup is error-prone without a pinned requirements file.

**2. Scan concurrency / rate limiting**
- `scanner.py` fetches yfinance data serially in a `for` loop over the full universe (potentially 500–1500 stocks).
- A full scan can take 30–90 minutes depending on universe size and network latency.
- No retry logic on transient yfinance failures.
- **Impact**: Long scan times; a network blip on any stock silently skips it (logged as warning only).

**3. No test suite**
- No unit or integration tests exist anywhere in the project.
- Strategy logic, scoring math, and DB operations are untested programmatically.
- **Impact**: Regressions in strategy logic are invisible until a live scan is inspected manually.

**4. `market_hunter.db` still tracked by git**
- The file was committed before `.gitignore` was updated.
- `.gitignore` now excludes `*.db`, but git will continue tracking the file until it is explicitly removed from the index.
- **Action required** — run this once in the Shell:
  ```bash
  git rm --cached market_hunter.db
  ```
  The file will remain on disk; only git tracking is removed.

---

## Known Risks

| Risk | Severity | Area |
|---|---|---|
| FMP free-tier rate limits (300 req/day) | High | `data/fmp.py` — screener is one call; price data uses yfinance so FMP limits are not a scan bottleneck |
| yfinance API changes / unofficial API | Medium | `data/price.py` — yfinance scrapes Yahoo Finance; breaking changes occur without notice |
| Duplicate signals on same-day re-runs | Medium | `database/db.py`, `scanner.py` |
| Scan runtime (serial, 500–1500 stocks) | Medium | `scanner.py` |
| ADRs passing universe filter | Low | `data/fmp.py` |
| No file logging on VPS | Low | `main.py` |
| No `requirements.txt` | Low | VPS setup only |

---

## Scoring Reference

| Component | Max | Logic |
|---|---|---|
| Trend Score | 30 | +8 close>MA20, +8 close>MA50, +7 close>MA200, +4 MA20>MA50, +3 full alignment |
| Relative Strength | 25 | RS vs SPY: 20-day (12pts) + 60-day (13pts), tiered by outperformance margin |
| Volume Score | 20 | Vol/VolMA20 ratio, tiered from 2pt (below avg) to 20pt (2×+ avg) |
| Pullback Risk | 15 | Distance from 52-week high, tiered from 1pt (>30% off) to 15pt (<3% off) |
| Sector Score | 10 | Hardcoded by sector: Technology=10 … Utilities=3, default=5 |
| **Total** | **100** | |
