# Binance AI Trader V1

The current implementation provides a read-only Binance USDⓈ-M Futures public-data layer, deterministic scoring engine, and deterministic regime-directed LONG/SHORT signal engine. It contains no authentication, account, order, AI-model, or web functionality; optional Telegram Bot API notifications are outbound-only.

## Capabilities

- Discover trading USDT perpetual contracts from `/fapi/v1/exchangeInfo`.
- Read all-symbol 24-hour statistics from `/fapi/v1/ticker/24hr`.
- Retain contracts with quote volume strictly greater than 5,000,000 USDT.
- Exclude configured stablecoins, leveraged-token suffixes, and denied symbols.
- Download and validate closed `15m`, `1h`, and `4h` candles from `/fapi/v1/klines`.
- Compute `TrendScore`, `VolumeScore`, `MomentumScore`, `StructureScore`, and `RiskScore`.
- Classify BTC and ETH market regimes from closed 15m, 1h, and 4h candles.
- Rank mapped sectors from the latest score and 24-hour universe snapshot.
- Read the latest Top 20 score ranking and select at most three regime-directed LONG pullback or SHORT rebound signals.
- Persist collection runs, universe snapshots, candles, scores, and signals to SQLite.
- Print one JSON Line per generated LONG or SHORT signal.

## Requirements

- Python 3.11+
- Network access to `https://fapi.binance.com`

No third-party runtime dependency is required.

## Run

```bash
PYTHONPATH=src python -m binance_ai_trader scan \
  --database data/market_data.db \
  --config config/universe.json \
  --kline-limit 200 \
  --max-workers 5
```

The CLI collects a fresh public-market snapshot, scores eligible symbols, generates up to three regime-directed signals from the Top 20, saves all results, and emits each signal as one compact JSON line:

```json
{"symbol":"BTCUSDT","direction":"LONG","score":82.5,"entry":"100.10","latest_close":"101.00","stop_loss":"97.90","stop_loss_pct":"2.20","TP1":"102.30","TP2":"105.00","rr_tp1":"1.00","rr_tp2":"2.23","logic_summary":"LONG pullback: ..."}
```

Exit code `0` means all requested datasets succeeded. Exit code `2` means one or more symbol/interval requests failed; affected symbols are excluded from scoring while complete symbols can still be scored and evaluated.

## Scoring model

The initial score is deterministic and bounded to 0–100:

| Component | Maximum | Main inputs |
|---|---:|---|
| TrendScore | 30 | 1h/4h EMA position, alignment, and 4h slope |
| VolumeScore | 20 | 15m/1h relative quote volume and 15m trade participation |
| MomentumScore | 20 | 1h/4h six-period ROC and 1h RSI |
| StructureScore | 15 | 1h range position, higher high/low, and 15m breakout |
| RiskScore | 15 | 1h ATR%, largest recent 15m candle, and opening gaps |

At least 40 closed 15m candles and 55 closed candles for each of 1h and 4h are required. Equal totals are ordered by symbol, making ranking deterministic.

## LONG signal rules

The signal engine preserves score rank and evaluates only the latest run's Top 20 symbols. It returns the first three candidates that pass every rule; it does not force three signals.

- **Entry:** a confirmed recent 15m or 1h swing-low support plus a small deterministic buffer, rounded to tick size. Entry must be between 3% below and 1% above the latest closed 15m close.
- **Stop:** the tighter valid level derived from a recent 1h swing low with ATR buffer or a 2× 1h ATR stop. Risk is widened to at least 2% to avoid noise; candidates above 7% risk are rejected.
- **TP1:** the nearest prior 1h/4h resistance at or above 1R, otherwise the exact 1R objective.
- **TP2:** a prior 1h/4h high or range boundary at or above 2R. A candidate without structural room for at least 2R is rejected.
- **Direction:** this section defines the LONG path; SHORT uses the separate regime-directed rules below.

The rules use only closed candles and decimal tick-size rounding. They do not invoke an AI or machine-learning model.

## Configuration

`config/universe.json` contains the liquidity threshold and exclusions. The volume comparison is strict (`volume24h > 5,000,000`). Financial market-data and signal-price fields are persisted as decimal strings to avoid binary floating-point loss at the data boundary.

## SQLite tables

- `collection_runs`: run status and failure summary.
- `universe_snapshots`: retained contracts and their 24-hour statistics.
- `klines`: idempotent closed candles keyed by symbol, interval, and open time.
- `scores`: full-run rank, total score, JSON breakdown, algorithm version, and timestamp.
- `capital_snapshots`: public OI changes, funding/crowding components, and Capital Score.
- `space_snapshots`: direction-specific 30/60/120-day distances and Space Score.
- `signals`: direction rank, LONG/SHORT direction, component/final scores, entry/latest close, stop, targets, RRs, explanation, and generation timestamp.
- `signal_evaluations`: deterministic outcome, excursions, bars to result, and evaluation timestamp.
- `market_regimes`: BTC, ETH, and combined regime history with evaluation timestamps.
- `sector_snapshots`: per-run sector metrics and deterministic strength rank.
- `strategy_versions`: immutable strategy configs, review status, creation time, and latest research metrics.

SQLite uses foreign keys, WAL mode, a busy timeout, and transactional writes.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The automated test suite uses local fixtures/fakes and does not call Binance.

## Paper signal evaluation

The evaluator measures stored LONG and SHORT signals against future closed 15m candles already present in SQLite. It never downloads private data and never places an order.

```bash
PYTHONPATH=src python -m binance_ai_trader evaluate \
  --database data/market_data.db
```

Evaluation is deterministic:

- only 15m candles whose close time is after the signal's `generated_at` are considered;
- a signal becomes active only after a candle trades through its Entry;
- at most 96 future candles (24 hours) are evaluated;
- LONG targets are hit by candle highs and LONG stops by candle lows; SHORT targets are hit by candle lows and SHORT stops by candle highs;
- a candle touching Stop and any target is a conservative `LOSS`;
- a TP2 touch is `WIN_TP2`;
- a TP1 touch without a later TP2 touch inside the complete window is `TP1_HIT`;
- no target or Stop after the complete 96-bar window is `EXPIRED`;
- an incomplete window with no terminal Stop/TP2 event remains pending and is not prematurely persisted as expired.

`max_favorable_pct` and `max_adverse_pct` are positive excursion magnitudes measured from Entry after activation. Summary rates use completed evaluations as the denominator; `tp1_hit_rate` includes both `TP1_HIT` and `WIN_TP2`, because every TP2 winner also crossed TP1.

The command emits one JSON summary containing counts, TP1/TP2/loss/expired rates, expectancy in R, average favorable/adverse excursions, and `by_direction` LONG/SHORT metrics. Completed results are idempotently saved in `signal_evaluations`.

## Market regime

The `regime` command reads only stored, closed `BTCUSDT` and `ETHUSDT` 15m, 1h, and 4h candles:

```bash
PYTHONPATH=src python -m binance_ai_trader regime \
  --database data/market_data.db
```

It emits only the three market-state fields:

```json
{"btc_regime":"BULL","eth_regime":"BULL","combined_regime":"BULL"}
```

Each asset uses EMA20, EMA50, and ATR deterministically. Matching 1h/4h EMA alignment produces `BULL` or `BEAR` when 15m does not contradict it. Non-trending alignment produces `RANGE`. Missing/invalid history, excessive ATR, timeframe conflict, or BTC/ETH directional conflict produces `OBSERVE`. The default high-volatility gates are 1h ATR above 5% or 4h ATR above 8%. At least 51 closed candles per timeframe are required. Every result is appended to `market_regimes`.

## Regime-gated signal directions

Signal generation reads the newest `market_regimes.combined_regime` before evaluating the latest Top 20 scores. If no regime history exists, the gate defaults to `OBSERVE`.

| Combined regime | Signal behavior |
|---|---|
| `BULL` | Evaluate candidates normally and emit up to three valid signals. |
| `RANGE` | Allow LONG strength or SHORT weakness only when the directional score is at least 85. |
| `BEAR` | Emit SHORT signals only. |
| `OBSERVE` | Emit no signals. |

The `scan` command runs in this order: collect closed public market data, analyze and persist BTC/ETH regime, score the market, calculate sector strength, then generate regime-gated and sector-aware LONG/SHORT signals. Each persisted and emitted signal includes the `combined_regime` used by the gate. The independent `regime` command remains available.

## Sector strength

`config/sectors.json` maintains the version-controlled `symbol_to_sector` mapping. Supported sectors are `AI_AGENT`, `RWA`, `MEME`, `DEPIN`, `INFRA`, `LAYER1`, `LAYER2`, `DEFI`, `GAMEFI`, and `OTHER`; every unmapped symbol is assigned to `OTHER`.

```bash
PYTHONPATH=src python -m binance_ai_trader sectors \
  --database data/market_data.db \
  --config config/sectors.json
```

The command joins the newest score run to the matching `universe_snapshots` run, calculates sector statistics, saves them to `sector_snapshots`, and emits one JSON Line per non-empty sector. Each row contains `sector`, `sector_rank`, `member_count`, `avg_score`, `median_score`, `top3_avg_score`, `positive_24h_ratio`, and `quote_volume_24h`. The positive ratio is represented from `0.0000` to `1.0000`.

Ranking is deterministic from strongest to weakest using, in order: `top3_avg_score`, `median_score`, `avg_score`, `positive_24h_ratio`, `quote_volume_24h`, and sector name as the final tie-breaker. The standalone `sectors` command only calculates and records sector statistics; the `scan` pipeline consumes those snapshots in the separate Sector Gate described below without changing scoring or Signal Engine calculations.

## Sector-aware Top 3 selection

The `scan` pipeline now runs in this order: collect market data, analyze BTC/ETH regime, score symbols, calculate sector strength, then generate regime-gated and sector-aware LONG/SHORT signals.

After loading the latest Top 20 scores, signal selection reads `sector_snapshots` from the same score run and attaches each candidate's mapped `sector` and `sector_rank`. When snapshots exist, candidates from stronger sectors are evaluated first while preserving score rank within a sector. The Sector Gate then applies:

| Sector context | LONG candidate requirement |
|---|---|
| `sector_rank <= 3` | No additional score threshold. |
| `sector_rank 4–6` | Score must be at least 85. |
| `sector_rank > 6` | Score must be at least 90. |
| `OTHER` | Score must be at least 90, regardless of rank. |
| No sector snapshot for the score run | Do not block or reorder candidates. |

The existing combined-regime gate still applies first. Every persisted and emitted signal now includes `sector` and nullable `sector_rank`. This selection layer does not change scoring weights or the Signal Engine's Entry, Stop Loss, TP, or RR calculations.

## Historical backtest validation

The `backtest` command replays the current deterministic LONG/SHORT strategy against closed klines already stored in SQLite. It does not call Binance or modify the scoring, regime, sector, Entry, Stop Loss, TP, or RR rules.

```bash
PYTHONPATH=src python -m binance_ai_trader backtest \
  --database data/market_data.db \
  --config config/sectors.json \
  --step-bars 1
```

Optional `--start-ms` and `--end-ms` arguments restrict evaluation timestamps. `--step-bars` controls how many eligible 15m timestamps are skipped between evaluations and defaults to every bar. An evaluation timestamp is eligible only when BTC and ETH both have a closed 15m bar at that time and at least 96 later BTC 15m bars are already stored.

At every timestamp, the replay performs the complete strategy chain using only candles whose `close_time_ms` is less than or equal to that timestamp: BTC/ETH regime, symbol scores, rolling 24-hour sector statistics, Regime Gate, Sector Gate, and regime-directed Top 3 LONG/SHORT signal generation. Signal outcomes use only the next 96 closed 15m bars. Universe metadata is selected from the latest collection snapshot observed no later than the evaluation timestamp. This point-in-time boundary prevents regime, score, sector, and signal calculations from reading future candles.

The command writes one JSON summary and persists the run to `backtest_runs` plus each completed signal outcome to `backtest_results`. Summary metrics are:

* `total_signals`: completed signal evaluations included in the report.
* `tp1_hit_rate`: percentage reaching TP1 or TP2.
* `tp2_win_rate`, `loss_rate`, and `expired_rate`: percentages by terminal result.
* `profit_factor`: gross positive R divided by gross loss R; JSON `null` when there are no losses.
* `expectancy_r`: average realized R per signal.
* `max_drawdown_r`: largest peak-to-trough decline in chronological cumulative R.
* `avg_rr_tp2`: average planned TP2 reward/risk ratio.

For deterministic accounting, `LOSS` realizes `-1R`, `TP1_HIT` realizes the signal's planned `rr_tp1`, `WIN_TP2` realizes its planned `rr_tp2`, and `EXPIRED` realizes `0R`. The same metrics are grouped under `by_direction`, `by_regime`/`by_combined_regime`, `by_sector`, and `by_score_bucket` (`90-100`, `80-90`, `70-80`, and `below 70`). These assumptions validate the existing rule set; they do not optimize or retune it.

## Strategy Lab / Auto Research

Strategy Lab is a research-only parameter registry and historical comparison layer. It can generate candidate parameter sets and replay them against the same stored SQLite history, but it cannot place orders, modify `scan`, replace `baseline_v1`, approve a candidate, or automatically activate a candidate in production.

The canonical baseline is stored at `config/strategies/baseline_v1.json`. It records the current behavior exactly:

- scoring weights: trend 30, volume 20, momentum 20, structure 15, risk 15;
- RANGE minimum directional score 85;
- sector medium/weak thresholds 85/90;
- entry distance -3% to +1%;
- maximum stop risk 7%;
- minimum TP2 RR 2.0;
- evaluation window 96 closed 15m bars.

Strategy versions are persisted in `strategy_versions` with `baseline`, `candidate`, `approved`, or `rejected` status. Registering the baseline is idempotent and its config/status are immutable through Strategy Lab. Candidates remain `candidate`; there is no automatic approval path and `scan` does not accept a candidate strategy option.

List registered versions:

```bash
PYTHONPATH=src python -m binance_ai_trader strategies list \
  --database data/market_data.db
```

Compare registered strategies on exactly the same eligible historical timestamps:

```bash
PYTHONPATH=src python -m binance_ai_trader strategies compare \
  baseline_v1 candidate_ID \
  --database data/market_data.db \
  --sectors-config config/sectors.json \
  --step-bars 1
```

Each JSON Line contains `strategy_id`, `trades`, `win_rate` (completed TP2 wins), `profit_factor`, `expectancy`, `max_drawdown`, and a `regime_breakdown` with the same metrics for BULL, BEAR, RANGE, and OBSERVE. Comparison is research-only and does not activate any strategy.

Strategy Lab Phase 1 also registers three file-configured research variants from `config/strategies/`:

- `range_disabled_v1` excludes RANGE-regime results;
- `bear_short_space80_v1` includes only BEAR SHORT results with `space_score >= 80`;
- `capital_60_80_space80_v1` includes results with capital score from 60 through 80 and `space_score >= 80`.

These filters are applied only to Strategy Lab backtest results. They do not alter the canonical baseline, production signal construction, scoring, the production runner, or Telegram behavior.

The legacy command spelling `auto_research` remains an alias for `auto-research`. Auto Research changes only configured parameters; it does not generate algorithm code. Only observation-gate-passing Top candidates are stored, always with `candidate` status. A candidate cannot be manually selected for a production-style run unless its status has first been changed to `approved` through a separate human review process; this release intentionally provides no automatic approval command.

## Regime-directed SHORT signals

The scan pipeline now supports deterministic SHORT analysis signals without account access or order placement. Direction is controlled only by the latest `combined_regime`:

| Combined regime | Allowed output |
|---|---|
| `BULL` | LONG only. |
| `BEAR` | SHORT only. |
| `RANGE` | LONG strength score or SHORT weakness score must be at least 85. |
| `OBSERVE` | No signals. |

For SHORT selection, the system still reads the latest Top 20 stored scores, converts each strength score into a deterministic weakness score (`100 - strength_score`), and considers weaker candidates first. The emitted `score` for a SHORT signal is this weakness score.

SHORT entries wait for a rebound toward a recent 15m or 1h swing-high resistance rather than chasing the current close. The stop is above Entry using a recent 1h swing high with ATR buffer or a 2× ATR level, widened to at least 2% and rejected above 7%. TP1 must provide at least 1R and TP2 must use prior 1h/4h support or a range low with at least 2R. A valid SHORT signal satisfies `stop_loss > entry > TP1 > TP2` and includes the same regime and sector audit context as LONG signals.

The `evaluate` and `backtest` commands use direction-aware outcome semantics for both LONG and SHORT signals. Neither command changes signal construction, Regime Gate, or Sector Gate rules.

## Auto Research and aggressive paper mode

These commands are research-only. They do not use Binance API keys, access an account, place orders, automatically approve a strategy, or guarantee that a 1,000 USDT paper balance will reach any target.

Run the parameter laboratory:

```bash
PYTHONPATH=src python -m binance_ai_trader auto-research \
  --database data/market_data.db \
  --sectors-config config/sectors.json
```

`auto-research` deterministically creates 20 parameter-only variants of `baseline_v1`, replays every variant over the same eligible historical timestamps, ranks completed backtests by higher `expectancy_r`, higher `profit_factor`, and lower `max_drawdown_r`, and applies an observation gate requiring at least one signal, positive expectancy, and profit factor above 1 (or no losing trades). It saves up to the Top 10 passing variants to `strategy_versions`. Every saved version has `candidate` status. Candidates are never approved or selected by `scan` automatically.

Apply completed LONG/SHORT evaluations to the paper ledger:

```bash
PYTHONPATH=src python -m binance_ai_trader paper-simulate \
  --database data/market_data.db
```

The ledger starts at 1,000 USDT. `AGGRESSIVE` mode risks 5% of current paper equity per evaluated signal and `NORMAL` mode risks 3%. Two consecutive losses downgrade the account to `NORMAL`; three consecutive losses set a 24-hour `PAUSED` period. Signals generated inside that period are recorded as skipped with zero risk. Non-loss outcomes reset the consecutive-loss counter. Milestone targets are 1,500, 2,500, 5,000, and 10,000 USDT. This is intentionally high-risk paper accounting, not a forecast or promise of returns.

Print the UTC daily research report:

```bash
PYTHONPATH=src python -m binance_ai_trader daily-report \
  --database data/market_data.db \
  --date 2026-06-06
```

The JSON report contains that day's LONG/SHORT signals, the latest same-day signal batch's existing rank-ordered `top3`, latest same-day BTC/ETH regime, latest same-day sector ranking, current paper equity and risk mode, latest Top 5 ranked candidate strategies, milestone target, and whether aggressive risk is currently allowed. This report does not rescore or rerank signals.

## Reserved VM production runner

For a persistent Replit Reserved VM deployment, run:

```bash
PYTHONPATH=src python -m binance_ai_trader run-loop \
  --database data/market_data.db
```

Optionally configure Telegram before starting the loop:

```bash
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"
```

Telegram receives failed runner task alerts and the successful daily Top3 report. The fault-isolated UTC scheduler runs scan/evaluate/paper simulation every 15 minutes, daily reporting at 00:05 UTC, the resumable `collect-history` job every 24 hours, and parameter-only auto research every six hours. Change only the history cadence with `--history-interval-hours`; `--history-days` controls the retained bootstrap window. Every attempt is audited in `runner_events`, and an OS file lock prevents two loops from using the same runner lock. Check current state with:

```bash
PYTHONPATH=src python -m binance_ai_trader health \
  --database data/market_data.db
```

See [`docs/replit_reserved_vm.md`](docs/replit_reserved_vm.md) for start/stop, logs, database backup, storage maintenance, and daily operator checks. The health payload includes SQLite `quick_check`, foreign-key violation count, and journal mode, and exits with status 2 when SQLite reports an unhealthy database. The runner remains read-only with respect to Binance: no API key, account access, or order placement is included.

## Capital Flow and Space analysis

The scan pipeline now runs `capital` and `space` analysis after scoring and sector ranking, before signal generation. Both engines use public market data only; they do not require credentials, account access, or order endpoints.

```bash
PYTHONPATH=src python -m binance_ai_trader capital --database data/market_data.db
PYTHONPATH=src python -m binance_ai_trader space --database data/market_data.db
```

`capital` reads public current/open-interest history, current funding, and the global long/short account ratio for the latest Top 20 symbols. It combines 24-hour volume expansion, 1h/4h/24h open-interest expansion, a funding-neutrality penalty, and a crowding penalty into a deterministic `capital_score` from 0 to 100. Results are persisted in `capital_snapshots`.

`space` uses 720 already-closed 4h candles (120 days) and measures distance to the 30/60/120-day highs and lows. It produces direction-specific `upside_pct`, `downside_pct`, and `space_score` values for LONG and SHORT, persisted in `space_snapshots`. During `scan`, missing 120-day 4h history is fetched from the public kline endpoint and saved before calculation.

Signal price construction is unchanged. Entry, stop loss, TP1, TP2, and RR are still produced by the existing LONG/SHORT engines. Only candidate ordering changes through `final_signal_score`:

- Capital Score: 30%
- Space Score: 30%
- Trend Score: 20%
- Sector Score: 10%
- Regime Score: 10%

The generator can emit up to three LONG and three SHORT signals when the existing Regime Gate permits those directions. JSON Lines and the `signals` table include `capital_score`, `space_score`, and `final_signal_score` for auditability.

Backtest summaries include `by_capital_bucket` and `by_space_bucket` groups (`0-40`, `40-60`, `60-80`, `80-100`). Each bucket reports expectancy and RR metrics so research can test whether stronger capital flow improves expectancy and whether greater directional space improves realized opportunity. No strategy parameters are optimized or activated by these statistics.

The daily paper report also includes `top_capital_long` and `top_capital_short`. Each row contains `symbol`, `direction`, `capital_score`, `space_score`, `entry`, `sl`, `tp1`, `tp2`, and `rr`.

## Walk-forward validation

Use rolling train/validation/test partitions to measure out-of-sample stability without
changing any strategy rule:

```bash
PYTHONPATH=src python -m binance_ai_trader walk-forward \
  baseline_v1 \
  --database data/market_data.db \
  --train-points 720 \
  --validation-points 240 \
  --test-points 240 \
  --step-points 240 \
  --embargo-points 96
```

For every rolling window, all supplied strategy configurations are compared on the training
slice only. The selected configuration is then evaluated once on the untouched validation
slice and once on the untouched test slice. A purge/embargo of at least the strategy
evaluation horizon separates each partition so training outcomes cannot consume candles from
the validation period and validation outcomes cannot consume candles from the test period. The command prints JSON and writes
`reports/walk_forward_validation.md` with train, validation, and test win rate, profit factor,
maximum drawdown, trade count, and automatic overfitting warnings. Win rate means the
existing TP1-or-better hit rate. The procedure does not approve candidates or change scan
behavior.

## Historical database bootstrap

Create or resume a public-data-only historical SQLite database:

```bash
PYTHONPATH=src python -m binance_ai_trader collect-history \
  --days 180 \
  --database data/market_data.db \
  --config config/universe.json \
  --sectors-config config/sectors.json
```

`collect-history` discovers the current eligible USD-M perpetual universe, always includes
BTCUSDT and ETHUSDT, and also includes valid symbols from `config/sectors.json`. It paginates
only public Binance endpoints and stores closed 15m, 1h, and 4h klines, daily historical
universe snapshots derived from trailing 24-hour kline volume, funding history, open-interest
history, global long/short ratio history, and rolling 24-hour quote-volume observations.

Writes are idempotent: klines are upserted by symbol/interval/open time, Capital Flow
observations are immutable and inserted only once, and completed daily universe snapshots are
skipped on rerun. Re-running the command repairs interrupted pages without duplicating market
rows. Binance's public open-interest and global long/short history endpoints have limited
historical retention, so a 180-day bootstrap can contain full kline/funding history but only
the public retention window for those two metrics. Data-quality metadata keeps later fallback
usage visible. No API key, account endpoint, balance endpoint, position endpoint, or order
endpoint is used.
