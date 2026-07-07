# market-hunter

A modular US stock daily scanner and research system. Scans large-cap US stocks for three technical setups, scores them across multiple dimensions, stores results in SQLite, and sends Telegram notifications.

**This is a scanner/research tool only. No auto-trading.**

---

## Features

- **Universe**: US common stocks with market cap ≥ $10B and avg daily dollar volume ≥ $50M (via FMP API)
- **3 Strategy Modules**:
  - MA60 Reclaim Pullback
  - Strong Trend Pullback (MA20 > MA50 > MA200)
  - New High Breakout with volume confirmation
- **Unified Scoring** (0–100): trend, relative strength vs SPY, volume, pullback risk, sector
- **Outputs**: Top 20 overall + Top 5 per strategy
- **Telegram Notifications**: formatted HTML report sent after each scan
- **SQLite Database**: signals, scan_runs, strategy_results, evaluations tables
- **Backtesting**: 5/10/20-day return, max drawdown, max gain after each signal
- **Scheduler**: daily scan at 06:30 Malaysia time (after US market close)

---

## Setup

### Required Environment Variables / Secrets

| Variable | Description |
|---|---|
| `FMP_API_KEY` | Financial Modeling Prep API key — [get one free](https://financialmodelingprep.com) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Telegram chat ID (use [@userinfobot](https://t.me/userinfobot) to find yours) |

Telegram is optional — the scanner works without it, just no notifications.

---

## Setup on Replit

1. Fork / open this project on [replit.com](https://replit.com)
2. Go to **Tools → Secrets** and add the three environment variables above
3. Open the Shell and run:

```bash
python main.py scan-us
```

Replit already has Python 3.11 and all dependencies installed.

---

## Setup on Linux VPS

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd market-hunter

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install yfinance requests apscheduler pytz

# 4. Set environment variables
export FMP_API_KEY="your_key_here"
export TELEGRAM_BOT_TOKEN="your_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"

# 5. Run a scan
python main.py scan-us
```

### Run as a background service (systemd)

Create `/etc/systemd/system/market-hunter.service`:

```ini
[Unit]
Description=Market Hunter Scheduler
After=network.target

[Service]
WorkingDirectory=/path/to/market-hunter
ExecStart=/path/to/venv/bin/python main.py schedule
Environment="FMP_API_KEY=your_key"
Environment="TELEGRAM_BOT_TOKEN=your_token"
Environment="TELEGRAM_CHAT_ID=your_chat_id"
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable market-hunter
sudo systemctl start market-hunter
sudo systemctl status market-hunter
```

---

## CLI Commands

```bash
# Run the scan immediately (fetches data, runs strategies, saves to DB, sends Telegram)
python main.py scan-us

# Evaluate historical signals (calculates 5/10/20-day returns for signals ≥5 days old)
python main.py evaluate-us

# Print a performance report to the terminal
python main.py report-us

# Start the daily scheduler (blocks — runs scan at 06:30 MYT every day)
python main.py schedule
```

---

## Project Structure

```
market_hunter/
├── config.py               # All configuration constants and env vars
├── scanner.py              # Main scan orchestrator
├── evaluator.py            # Backtest / signal evaluation engine
├── report.py               # CLI report printer
├── data/
│   ├── fmp.py              # FMP API — universe, market cap, sector
│   └── price.py            # yfinance OHLCV + indicator computation
├── strategies/
│   ├── ma60_reclaim.py     # Strategy A: MA60 reclaim pullback
│   ├── strong_trend.py     # Strategy B: strong trend pullback
│   └── new_high.py         # Strategy C: new high breakout
├── scoring/
│   └── scorer.py           # Unified 0–100 scoring system
├── telegram/
│   └── notifier.py         # Telegram notification sender
├── database/
│   └── db.py               # SQLite schema + CRUD helpers
└── scheduler/
    └── runner.py           # APScheduler daily job
main.py                     # CLI entry point
```

---

## Scoring Breakdown

| Component | Max | Description |
|---|---|---|
| Trend Score | 30 | MA alignment (MA20/50/200) |
| Relative Strength | 25 | vs SPY over 20 and 60 days |
| Volume Score | 20 | Recent volume vs 20-day avg |
| Pullback Risk | 15 | Distance from 52-week high |
| Sector Score | 10 | Sector momentum bias |
| **Total** | **100** | |

---

## Strategy Details

### A. MA60 Reclaim Pullback
Stocks that broke above their 60-day moving average after a downtrend, and have since pulled back to test it as support. Classic setup for continuation.

### B. Strong Trend Pullback
Stocks in a healthy uptrend (MA20 > MA50 > MA200), within 15% of their 52-week high, that have pulled back near MA20 or MA50. Low-risk entry in a proven trend.

### C. New High Breakout
Stocks breaking out to a new 52-week high with at least 1.5× average volume. Momentum-driven, favors continuation.

---

## Backtesting / Evaluation

Run `python main.py evaluate-us` after signals are at least 5 trading days old. It calculates:

- **5-day return** — short-term momentum
- **10-day return** — medium-term follow-through
- **20-day return** — monthly performance
- **Max drawdown** — worst intra-period dip from entry
- **Max gain** — best intra-period gain from entry

Results are stored in the `evaluations` table and shown in `report-us`.
