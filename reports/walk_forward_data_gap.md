# Walk-Forward Validation Data Gap

## Audit result

No usable SQLite market database exists in the repository or elsewhere under `/workspace`.
The repository contains only `data/.gitkeep`; no `*.db`, `*.sqlite`, or `*.sqlite3` file was
found. The only market-data fixture, `tests/fixtures/klines.json`, contains three 15-minute
candles and is an HTTP parsing fixture rather than a populated historical backtest dataset.
It has no matching 1-hour/4-hour history, universe snapshots, or Capital Flow observations.

A schema-only probe database was created outside the repository at
`/tmp/walk_forward_probe.db` and the real command was executed:

```bash
PYTHONPATH=src python -m binance_ai_trader walk-forward \
  baseline_v1 \
  --database /tmp/walk_forward_probe.db \
  --report /tmp/walk_forward_probe.md
```

The command correctly refused to generate performance statistics:

```text
ValueError: insufficient evaluation points: need at least 1392, got 0
```

The probe database contained zero rows in `klines`, `universe_snapshots`,
`capital_flow_observations`, and `backtest_results`, and the repository query returned zero
eligible evaluation timestamps.

## Requested metrics

Real out-of-sample metrics cannot be calculated from the available files. Reporting zeros as
performance would be misleading because no validation window was executed and no trades were
evaluated.

| Metric | Result |
| --- | --- |
| Number of completed windows | Not available — 0 eligible timestamps |
| Train win rate | Not calculable |
| Validation win rate | Not calculable |
| Test win rate | Not calculable |
| Train/validation/test profit factor | Not calculable |
| Train/validation/test max drawdown | Not calculable |
| Number of trades | Not calculable |
| No-trade windows | Not applicable — no window could be formed |
| Overfitting warnings | Not calculable without train/validation/test results |

This is a **data availability failure**, not evidence that the strategy has zero trades or
zero performance.

## Minimum data required for the first real run

### 1. Eligible walk-forward timestamps

With the current default policy, one complete rolling window requires:

- 720 training evaluation points;
- 96 purged/embargo points after training;
- 240 validation evaluation points;
- 96 purged/embargo points after validation;
- 240 test evaluation points.

Total: **1,392 chronologically ordered eligible 15-minute timestamps**. At uninterrupted
15-minute frequency this spans 14.5 days between the first and last candidate timestamps.
More history is needed for multiple rolling windows; every additional default window requires
240 later eligible timestamps (2.5 days).

Each eligible timestamp must have matching closed BTCUSDT and ETHUSDT 15-minute candles and
at least 96 later BTCUSDT 15-minute candles, because the baseline evaluation horizon is 96
bars. At least one additional day of future 15-minute candles must therefore remain after the
last test timestamp so outcomes can be evaluated.

### 2. Warm-up candles available before every timestamp

The backtest reads only candles closed at or before the evaluation timestamp. The database
must contain, for BTCUSDT, ETHUSDT, and every candidate symbol:

- at least 40 closed 15-minute candles for scoring;
- at least 55 closed 1-hour candles for scoring;
- at least 55 closed 4-hour candles for scoring;
- at least 51 closed candles on each of 15m, 1h, and 4h for BTC/ETH regime analysis;
- **720 closed 4-hour candles (120 days)** for complete Space analysis;
- enough later 15-minute candles to evaluate each signal for up to 96 bars.

Because the 720-bar Space requirement dominates, a complete-quality run needs at least 120
days of 4-hour history before the first walk-forward timestamp, plus the walk-forward period
and future evaluation horizon.

### 3. Historical universe lineage

`universe_snapshots` and their parent `collection_runs` must exist with timestamps no later
than each evaluation point. Every universe row must include the symbol and tick size used by
signal price rounding. Without a historical universe, the backtest intentionally produces no
signals even if candles exist.

For realistic sector statistics, the corresponding universe snapshots also need historical
24-hour price change and quote-volume values for the symbols included at that point.

### 4. Historical Capital Flow observations

For each candidate symbol and evaluation period, `capital_flow_observations` must contain
point-in-time observations for:

- open interest, including observations needed to reconstruct 1h, 4h, and 24h changes;
- funding rate;
- global long/short ratio;
- 24-hour quote volume.

Every observation must have an `observed_at_ms` at or before the evaluation timestamp and
retain snapshot lineage. Missing or stale observations remain visible through the existing
data-quality status, but extensive fallback data would not constitute a complete Capital
Flow validation.

### 5. Closed-candle and continuity checks

Before running validation, verify:

- no candle is duplicated by `(symbol, interval, open_time_ms)`;
- `close_time_ms` is ordered and no later candle is visible to an earlier evaluation point;
- BTCUSDT and ETHUSDT have aligned 15-minute timestamps;
- gaps are measured and documented rather than silently forward-filled;
- the final timestamp leaves 96 future 15-minute bars;
- snapshot and data-quality lineage migrations complete successfully.

## Collection target

A practical first dataset should cover at least **136 consecutive days**:

- 120 days of 4-hour warm-up history;
- at least 14.5 days for one default walk-forward window;
- at least one additional day for the final 96-bar outcome horizon.

For useful rolling evidence rather than a single fold, 150–180 days is preferable. It should
include BTCUSDT, ETHUSDT, and the historically selected universe across 15m, 1h, and 4h, plus
historical public Capital Flow observations. The current `scan` command's default 200-candle
fetch is not sufficient to reconstruct 720 historical 4-hour bars by itself; a dedicated
historical backfill or an imported audited database is required before claiming real
walk-forward metrics.

## Command to run after data collection

```bash
PYTHONPATH=src python -m binance_ai_trader walk-forward \
  baseline_v1 \
  --database data/market_data.db \
  --train-points 720 \
  --validation-points 240 \
  --test-points 240 \
  --step-points 240 \
  --embargo-points 96 \
  --report reports/walk_forward_validation.md
```

The generated report will then contain actual per-window train, validation, and test win
rates, profit factors, maximum drawdowns, trade counts, no-trade warnings, and overfitting
warnings.

## Safety and strategy integrity

This audit did not change scoring, strategy parameters, signal generation, ranking, entry,
stop loss, take profit, RR, or any LONG/SHORT rule. It did not add API keys, exchange account
access, order execution, or live trading functionality.
