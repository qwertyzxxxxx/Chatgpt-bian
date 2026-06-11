# Binance AI Trader V1 — Project Audit

> Audit date: 2026-06-07
> Repository: `/workspace/Chatgpt-bian`
> Scope: full repository structure, `roadmap.md`, `architecture.md`, runtime configuration, strategy engines, application orchestration, SQLite persistence, CLI, runner, research tools, and automated tests.
> Change boundary: this audit is documentation-only. No production or strategy logic was changed.

## Executive summary

The repository is no longer an empty V1 scaffold. It is a substantial, standard-library-only Python application with approximately 5,500 lines of source code, 100 automated test methods, 16 SQLite tables, and a CLI covering collection, analysis, signal generation, evaluation, backtesting, strategy research, paper simulation, reporting, and scheduled operation.

The current implementation is best described as a **modular analytical monolith**:

- public Binance USD-M Futures data enters through one read-only REST client;
- application services orchestrate deterministic domain engines;
- a single SQLite repository persists all operational and research state;
- a single CLI exposes both one-shot workflows and the long-running scheduler;
- no API key, account access, order placement, Telegram, or web dashboard exists.

The strongest parts of the repository are its deterministic rules, closed-candle discipline, explicit read-only boundary, broad fixture-based test coverage, and end-to-end SQLite workflows. The largest risks are architectural and research-validity risks rather than missing trading features: documentation drift, a 1,500-line repository class, a 550-line CLI, hard-coded policy duplicated outside the strategy registry, incomplete point-in-time capital-flow history, silent degradation paths, and backtest/research methodology that does not yet provide robust out-of-sample evidence.

The immediate recommendation is **not to add more signal rules**. The next engineering phase should first consolidate contracts, temporal lineage, migrations, observability, and research validation.

---

## 1. Current architecture

### 1.1 Architectural style

The application follows a layered modular-monolith structure under `src/binance_ai_trader/`:

```text
CLI / Runner
    |
    v
Application orchestration
    |
    +--> Public Binance client
    +--> Deterministic domain engines
    +--> SQLite repository
    |
    v
SQLite operational + research database
```

The boundaries are recognizable but not fully enforced through interfaces:

| Layer | Current responsibility | Main locations |
|---|---|---|
| Entrypoint | Parse CLI arguments, assemble dependencies, print JSON/JSONL | `entrypoints/cli.py`, `__main__.py` |
| Scheduling | Run periodic tasks, isolate failures, enforce single instance | `runner/engine.py`, `runner/health.py` |
| Application | Coordinate repositories, clients, and engines | `application/*.py` |
| Domain models | Shared immutable dataclasses for market data, signals, evaluations, and backtests | `domain/models.py` |
| Strategy/domain engines | Regime, score, sector, capital, space, LONG/SHORT signal rules, evaluation | `regime/`, `scoring/`, `sectors/`, `capital/`, `space/`, `signals/`, `evaluation/` |
| Research | Point-in-time replay and parameter-only candidate comparison | `backtest/`, `strategy_lab/` |
| Paper operations | Apply completed evaluations to a simulated equity ledger | `paper/` |
| Reporting | Build daily local reports | `reporting/` |
| Infrastructure | Public HTTP access and SQLite persistence/migrations | `infrastructure/` |
| Configuration | Universe, sector map, and baseline strategy parameters | `config/` |

### 1.2 Runtime topology

There are two supported operating modes:

1. **One-shot CLI commands** for scans, analyses, evaluation, backtesting, and reports.
2. **Reserved VM loop** through `run-loop`, which invokes:
   - `scan` every 15 minutes;
   - `evaluate` every 15 minutes;
   - `paper-simulate` every 15 minutes;
   - `daily-report` at 00:05 UTC;
   - `auto-research` every six hours.

Runner failures are recorded in `runner_events` and do not terminate the process. A file lock provides single-process protection on one filesystem.

### 1.3 Technology baseline

- Python 3.11+
- standard library only at runtime;
- `urllib`-based HTTP client;
- SQLite with foreign keys, WAL mode, and busy timeout;
- `unittest`-based unit and integration tests;
- source-layout packaging through `pyproject.toml`;
- no service framework, queue, ORM, or dependency-injection library.

This baseline is operationally simple and appropriate for a small Reserved VM, but the growing file sizes show that the monolith now needs internal decomposition.

### 1.4 Persistence architecture

The SQLite database currently contains the following logical groups:

| Group | Tables |
|---|---|
| Runner/operations | `runner_events`, `collection_runs` |
| Market data | `universe_snapshots`, `klines` |
| Analysis | `scores`, `market_regimes`, `sector_snapshots`, `capital_snapshots`, `space_snapshots` |
| Signals/evaluation | `signals`, `signal_evaluations` |
| Research | `backtest_runs`, `backtest_results`, `strategy_versions` |
| Paper accounting | `paper_accounts`, `paper_trades` |

The schema supports substantial auditability, but there is no explicit schema-version table or migration sequence. Migration behavior is embedded in `MarketDataRepository._migrate()` and several introspection/rebuild helpers.

### 1.5 Safety boundary

The repository currently maintains the intended read-only safety boundary:

- public market endpoints only;
- no Binance API key or secret configuration;
- no account, balance, position, or order endpoint;
- no execution adapter;
- no Telegram or web delivery surface;
- candidates from Strategy Lab are not automatically approved or selected by `scan`.

---

## 2. Data flow

### 2.1 Primary scan pipeline

The current `scan` path is:

```text
Binance public REST
    |
    v
1. Collect exchange info + 24h tickers
    |
    +--> Universe Filter
    |      - TRADING USDT perpetuals
    |      - quote volume > 5M USDT
    |      - stablecoin / leveraged-token exclusions
    |
    v
2. Collect closed 15m / 1h / 4h klines
    |
    +--> persist collection run, universe, klines
    |
    v
3. Analyze BTC/ETH combined regime
    |
    v
4. Score eligible symbols
    |
    v
5. Rank sector strength
    |
    v
6. Analyze capital flow for latest Top 20
    |
    v
7. Analyze 30/60/120-day directional space
    |
    v
8. Apply Regime Gate + Sector Gate
    |
    v
9. Construct valid LONG/SHORT opportunities
    |
    v
10. Rank with final_signal_score
    |
    v
11. Persist and print up to Top 3 per permitted direction
```

### 2.2 Collection flow

`MarketDataCollector` creates a collection run, builds the eligible universe, and downloads three kline intervals with bounded concurrency. It persists successful data and records partial failures without aborting the whole scan.

Important semantics:

- candles are filtered to closed candles by the public client;
- OHLC validity and interval continuity are checked;
- universe membership is tied to a collection `run_id`;
- symbol/interval failures exclude affected symbols from the scoring stage;
- the Capital Flow analyzer makes additional public requests after scoring;
- the Space analyzer may fetch 720 closed 4h candles for Top 20 symbols if SQLite lacks sufficient history.

### 2.3 Analysis flow

#### Regime

BTCUSDT and ETHUSDT are independently classified from 15m, 1h, and 4h candles using EMA20, EMA50, and ATR%. Their states are combined into `BULL`, `BEAR`, `RANGE`, or `OBSERVE`.

#### Symbol score

Each eligible symbol receives five component scores:

- TrendScore;
- VolumeScore;
- MomentumScore;
- StructureScore;
- RiskScore.

The baseline weights total 100 and are persisted with a detailed score breakdown.

#### Sector strength

The latest score run is joined to the corresponding universe snapshot. Symbols are mapped to configured sectors or `OTHER`, then grouped into sector-level averages, median, Top-3 average, positive 24h ratio, and quote volume. Ranking is deterministic.

#### Capital flow

For the latest Top 20 scored symbols, the system reads:

- current Open Interest;
- 1h Open Interest history used for 1h/4h/24h changes;
- current funding rate;
- current global long/short account ratio;
- current 24h quote volume and a local historical-volume baseline.

These inputs produce a 0–100 Capital Score.

#### Directional space

For each Top 20 symbol, the system reads 720 closed 4h candles and calculates distance to 30/60/120-day highs and lows. LONG and SHORT each receive a direction-specific Space Score.

### 2.4 Signal flow

The latest Top 20 strength scores are transformed into directional opportunities:

- LONG uses the original symbol score;
- SHORT uses `100 - strength score` as a weakness score;
- Regime Gate determines whether LONG, SHORT, both, or neither are eligible;
- Sector Gate currently filters LONG opportunities according to sector rank and score thresholds;
- missing sector snapshots do not block candidates;
- missing capital/space snapshots degrade to neutral score 50.

Price construction remains separate from ranking:

- LONG waits near recent 15m/1h support;
- SHORT waits near recent 15m/1h resistance;
- stop placement uses recent 1h swings and ATR;
- TP1 requires at least 1R;
- TP2 requires at least the configured minimum, currently 2R;
- invalid price ordering, excessive stop distance, insufficient history, or insufficient structural room rejects the candidate.

Valid opportunities are sorted by:

```text
final_signal_score =
    Capital Score * 30%
  + Space Score   * 30%
  + Trend Score   * 20%
  + Sector Score  * 10%
  + Regime Score  * 10%
```

The result can contain up to three LONG and three SHORT signals when the existing gates permit both directions.

### 2.5 Evaluation and paper flow

```text
Persisted signal
    |
    v
Future closed 15m bars (maximum 96)
    |
    +--> wait until Entry is touched
    +--> conservative same-bar TP/SL ordering: LOSS
    +--> LOSS / TP1_HIT / WIN_TP2 / EXPIRED
    |
    v
signal_evaluations
    |
    v
paper-simulate
    |
    +--> risk 5% in AGGRESSIVE or 3% in NORMAL
    +--> two losses downgrade mode
    +--> three losses pause for 24 hours
    |
    v
paper_accounts + paper_trades + daily report
```

### 2.6 Backtest and research flow

Backtest rolls through eligible historical 15m evaluation timestamps. At each point it loads only candles at or before that timestamp, recomputes regime, symbol scores, sectors, directional candidates, signal levels, and future evaluation results.

Strategy Lab:

1. registers `baseline_v1`;
2. creates deterministic parameter variants;
3. runs each variant over common evaluation timestamps;
4. ranks by expectancy, profit factor, and drawdown;
5. saves only passing variants as `candidate`;
6. never automatically approves or activates a candidate.

---

## 3. Existing strategies

This section distinguishes actual executable strategy behavior from aspirational descriptions in `architecture.md` and `roadmap.md`.

### 3.1 Universe strategy

**Purpose:** reduce the Binance USD-M universe to liquid, conventional USDT perpetual contracts.

Current rules:

- `contractType == PERPETUAL`;
- `status == TRADING`;
- quote and margin assets are USDT;
- 24h quote volume strictly greater than 5,000,000 USDT;
- configured stablecoin base assets excluded;
- leveraged-token suffixes excluded;
- explicit denylist supported.

### 3.2 Market Regime strategy

**Inputs:** BTCUSDT and ETHUSDT closed 15m, 1h, and 4h candles.

**Indicators:** EMA20, EMA50, ATR%.

**States:**

- `BULL`: aligned bullish trend conditions;
- `BEAR`: aligned bearish trend conditions;
- `RANGE`: non-trending but valid conditions;
- `OBSERVE`: insufficient, conflicting, or high-volatility conditions.

**Combination behavior:** both BTC and ETH must align for directional BULL/BEAR. Conflict or invalidity moves the combined state toward OBSERVE; otherwise mixed non-conflicting states become RANGE.

### 3.3 Baseline symbol scoring strategy

The canonical configuration is `config/strategies/baseline_v1.json`.

| Component | Baseline weight | Main behavior |
|---|---:|---|
| Trend | 30 | 1h/4h close and EMA20/EMA50 alignment, plus 4h EMA slope |
| Volume | 20 | recent 15m/1h quote-volume expansion and 15m trade-count participation |
| Momentum | 20 | multi-timeframe rate of change and RSI-derived momentum quality |
| Structure | 15 | range position, higher high/higher low, 15m breakout |
| Risk | 15 | ATR%, largest recent candle, and gap behavior |

The total is deterministic and bounded to 0–100. Minimum data requirements are enforced by `ScoringEngine`.

### 3.4 Sector strength strategy

Supported sectors are:

- AI_AGENT;
- RWA;
- MEME;
- DEPIN;
- INFRA;
- LAYER1;
- LAYER2;
- DEFI;
- GAMEFI;
- OTHER.

Sector rank is determined by, in order:

1. Top-3 average score;
2. median score;
3. average score;
4. positive 24h ratio;
5. aggregate 24h quote volume;
6. sector name as deterministic tie-breaker.

The current map contains 30 explicitly mapped symbols; every unmapped contract becomes `OTHER`.

### 3.5 Capital Flow strategy

Capital Score is a 0–100 composite:

| Component | Weight inside Capital Score |
|---|---:|
| Volume expansion | 30% |
| Open Interest expansion | 35% |
| Funding neutrality | 20% |
| Long/short crowding | 15% |

Open Interest expansion weights the 1h, 4h, and 24h changes at 20%, 30%, and 50%. Funding is most favorable near neutral and is penalized symmetrically at extreme positive or negative values. Crowding is most favorable near a long/short ratio of 1.

### 3.6 Space strategy

Space is measured from the latest close to rolling high/low extremes across:

- 30 days: 180 closed 4h bars;
- 60 days: 360 closed 4h bars;
- 120 days: 720 closed 4h bars.

LONG Space Score uses maximum upside to the selected highs. SHORT Space Score uses maximum downside to the selected lows. Directional room is multiplied by five and capped to 0–100.

### 3.7 LONG signal strategy

The LONG engine is a deterministic pullback strategy:

- requires recent 15m or 1h swing-low support;
- Entry must remain between -3% and +1% of the latest close;
- Entry is buffered and rounded to tick size;
- Stop uses a recent 1h swing low or ATR-based level;
- risk is widened to avoid sub-2% noise but rejected above 7%;
- TP1 uses structural resistance or exact 1R;
- TP2 requires structural room of at least 2R;
- malformed or underqualified setups are skipped rather than forced.

### 3.8 SHORT signal strategy

The SHORT engine mirrors the LONG approach directionally:

- seeks weak symbols through `100 - strength score`;
- waits for a rebound toward 15m or 1h swing-high resistance;
- places Stop above Entry using 1h swing high/ATR logic;
- requires downside targets with TP1 at least 1R and TP2 at least 2R;
- rejects excessive risk, invalid price ordering, and insufficient downside structure.

### 3.9 Regime Gate strategy

| Combined regime | Direction policy |
|---|---|
| BULL | LONG only |
| BEAR | SHORT only |
| RANGE | LONG and SHORT may qualify, directional score must be at least 85 |
| OBSERVE | no signals |

### 3.10 Sector Gate strategy

For LONG opportunities:

- sector rank 1–3: normal admission;
- sector rank 4–6: score must be at least 85;
- sector rank above 6 or `OTHER`: score must be at least 90;
- no sector snapshot: do not block.

The final directional sector contribution is separately computed in Signal Ranking V2. For SHORT ranking, weaker sector ranks receive the stronger ranking contribution. However, the explicit Sector Gate admission check is currently applied only to LONG in `SignalGenerator` and Backtest.

### 3.11 Signal Ranking V2

Valid signals are ranked by Capital 30%, Space 30%, directional Trend 20%, directional Sector 10%, and Regime 10%.

Important fallback behavior:

- missing Capital Score becomes 50;
- missing Space Score becomes 50;
- missing sector rank becomes 50 at the ranking layer;
- RANGE receives a lower Regime contribution than directional BULL/BEAR.

This ranking does not modify Entry, Stop, TP1, TP2, or RR.

### 3.12 Evaluation strategy

Evaluation supports LONG and SHORT and waits for actual Entry activation. It scans up to 96 future closed 15m bars.

- Stop takes precedence over target if both occur in one candle;
- TP2 is a terminal win;
- TP1 without TP2 over a complete window is `TP1_HIT`;
- no event over a complete window is `EXPIRED`;
- incomplete future history remains pending.

### 3.13 Backtest strategy

The backtest reconstructs the chain point in time and reports:

- aggregate performance;
- by direction;
- by regime;
- by sector;
- by symbol-score bucket;
- by Capital Score bucket;
- by Space Score bucket.

Metrics include hit rates, profit factor, expectancy in R, maximum drawdown in R, and average TP2 RR.

### 3.14 Strategy Lab

`baseline_v1` configures:

- five scoring weights;
- RANGE minimum score;
- medium/weak sector thresholds;
- entry-distance range;
- maximum stop-loss percentage;
- minimum TP2 RR;
- evaluation window.

Auto Research creates 20 predefined parameter-only variants, backtests them on common timestamps, applies a minimal observation gate, ranks them, and stores up to ten as `candidate`. There is no automatic approval path.

### 3.15 Aggressive paper strategy

The paper ledger is not an execution strategy. It models account outcomes using completed signal evaluations:

- starting equity: 1,000 USDT;
- AGGRESSIVE risk: 5%;
- NORMAL risk: 3%;
- two consecutive losses downgrade to NORMAL;
- three consecutive losses pause processing for 24 hours;
- milestones: 1,500 / 2,500 / 5,000 / 10,000 USDT.

---

## 4. Missing components

“Missing” here means absent or insufficient for a reliable production-grade research system. It does not imply that live trading should be added.

### 4.1 Versioned market-data lineage

The database does not expose a first-class immutable **analysis snapshot** tying together one collection run, exact candle cutoff, regime, score set, sector set, capital set, space set, and signal run. Some records use `run_id`, while market regime is loaded as the latest global record. A scan can therefore consume analysis artifacts with different timestamps if commands overlap or are manually invoked.

### 4.2 Complete historical capital-flow dataset

Historical backtesting can only use Capital snapshots that were previously collected. Klines alone cannot reconstruct historical Open Interest, funding, or long/short ratios. Missing historical Capital Score silently becomes 50, so historical Capital-bucket results may be sparse or misleading.

### 4.3 Explicit data-quality status for derived analyses

Capital collection skips a symbol on any exception and Space may simply produce no snapshot when history is insufficient. There is no persisted status/reason table for “missing”, “stale”, “API failed”, “insufficient history”, or “defaulted to neutral”. Consumers cannot distinguish a true score of 50 from a fallback value of 50.

### 4.4 Proper migration/version management

Schema evolution is handled through `CREATE TABLE IF NOT EXISTS`, `PRAGMA table_info`, SQL-string inspection, and table rebuilds. There is no ordered schema version, migration history, rollback policy, or backup-before-migration workflow.

### 4.5 CI, linting, typing, and coverage enforcement

The roadmap calls for Ruff, type checking, coverage, and CI, but the repository contains no CI workflow or tool configuration enforcing those checks. Tests are strong for behavior but do not replace static analysis and coverage thresholds.

### 4.6 Reproducible dependency/environment lock

Runtime has no third-party dependencies, but build tooling is unpinned beyond `setuptools>=68`; there is no lockfile, Python patch-version pin, or reproducible build manifest.

### 4.7 Out-of-sample research protocol

Backtest and Auto Research do not provide walk-forward splits, train/validation/test partitions, embargo periods, minimum sample requirements, confidence intervals, bootstrap analysis, or multiple-testing correction. Candidate ranking can therefore select noise.

### 4.8 Portfolio-level simulation

Signals are evaluated independently. There is no model for overlapping positions, simultaneous risk, symbol correlation, sector concentration, capital constraints, fees, funding payments, slippage, latency, or partial fills. Paper equity applies completed outcomes sequentially but is not a time-ordered portfolio simulator.

### 4.9 Data retention and maintenance policy

There is no automated retention, archival, VACUUM/checkpoint policy, database integrity schedule, or size threshold despite continuous 15-minute operation and large 4h history pulls.

### 4.10 Structured observability

Runner events are persisted, but there are no structured request metrics, per-endpoint latency/rate-limit counters, analysis coverage metrics, stale-data alarms, or explicit scan completeness score. Logs and SQLite records remain the primary diagnostic tools.

### 4.11 Strategy artifact completeness

`baseline_v1` does not contain every effective strategy parameter. Capital weights, Space scaling, V2 ranking weights, regime thresholds, and some signal-engine constants remain hard-coded. Consequently, a strategy version does not fully describe the behavior being backtested.

### 4.12 Documentation synchronization process

`roadmap.md` and `architecture.md` still describe the repository as empty and list many completed items as unchecked. They also retain Telegram as an intended V1 milestone even though repeated product constraints currently prohibit it. There is no documentation status/version process to prevent this drift.

---

## 5. Technical debt

### 5.1 Documentation debt — high

`roadmap.md` is dated 2026-06-05 but still states that the repository only contains `.gitkeep`. All 135 task checkboxes remain unchecked despite implementation of most M0–M6 analytical capabilities. `architecture.md` is similarly an original greenfield design rather than an as-built architecture.

Impact:

- reviewers cannot tell planned from implemented behavior;
- obsolete goals such as Telegram remain mixed with current prohibitions;
- acceptance criteria are not traceable to tests or releases.

### 5.2 SQLite repository size and responsibility — high

`sqlite_repository.py` is approximately 1,543 lines and owns:

- schema creation;
- migrations;
- runner events;
- collection state;
- market data;
- scoring;
- sectors;
- capital/space;
- signals;
- evaluations;
- backtests;
- strategy versions;
- paper accounts;
- reports.

This is a high-coupling “god repository”. Any schema change risks unrelated workflows, and tests must instantiate the entire database layer.

### 5.3 CLI size and composition — high

`entrypoints/cli.py` is approximately 551 lines and directly assembles every application workflow. Argument definitions, dependency construction, command execution, JSON serialization, and runner callbacks are centralized in one module.

### 5.4 Temporal consistency and race potential — high

`SignalGenerator` reads latest scores, latest combined regime, sector ranks for the score run, and capital/space scores for the score run. Regime is not tied to that run. Concurrent manual commands or overlapping scheduler activity could mix states from different snapshots.

### 5.5 Silent Capital degradation — high

`CapitalFlowAnalyzer` catches `Exception` and continues without persisting the failure. This protects the scan but hides endpoint, parsing, rate-limit, or data sufficiency problems. Signal ranking then substitutes 50, making missing data look neutral rather than unknown.

### 5.6 Backtest equivalence gap — high

The live chain can fetch current public capital data and a 120-day Space window. Historical replay cannot reconstruct missing historical capital features and defaults them to 50. Therefore, backtest behavior is not guaranteed to match live Signal Ranking V2.

### 5.7 Strategy configuration fragmentation — medium/high

The baseline strategy file configures core score/gate/risk parameters, but the following remain outside it:

- Capital component weights and scaling;
- funding/crowding breakpoints;
- Space multiplier and horizons;
- final ranking weights;
- regime-ranking contribution;
- signal kline windows and pivot periods;
- minimum stop widening behavior;
- paper risk percentages and loss rules.

This weakens reproducibility and makes `strategy_id` an incomplete behavioral identifier.

### 5.8 SHORT semantics are approximate — medium/high

SHORT candidate strength is derived as `100 - long-oriented score`. Several scoring components were originally designed as bullish-quality measures, so inversion may not represent bearish quality consistently. The final ranking corrects trend/sector directionally, but the base weakness score remains a coarse proxy.

### 5.9 Sector model fragility — medium

The sector map is manually maintained and currently maps only 30 symbols. `OTHER` can contain a large, heterogeneous group. Sector ranking has no minimum member count or concentration adjustment in executable code, despite those controls appearing in the original roadmap.

### 5.10 Research overfitting risk — high

Auto Research evaluates 20 known variants on the same history used for ranking. The observation gate requires positive expectancy and profit factor above one (or no losses), but there is no minimum trade count. A candidate with very few signals can rank highly.

### 5.11 Intrabar model simplification — medium

The evaluator conservatively treats any same-candle Stop/TP collision as LOSS, which is safe but coarse. It also assumes full execution at specified levels without spread, slippage, fees, funding, or market gaps. Results should be interpreted as rule-path outcomes, not executable P&L.

### 5.12 Operational durability — medium

The runner is single-process and fault-isolated, but scheduling state depends on runner-event timestamps. There is no lease renewal, stale-lock recovery policy beyond filesystem semantics, process supervisor integration, graceful shutdown state, or task timeout.

### 5.13 Exception taxonomy — medium

The public client defines a dedicated error type, but orchestration frequently catches broad exceptions. Data-invalid, transient network, rate-limit, persistence, and programming errors are not consistently separated.

### 5.14 Test architecture — medium

The repository has 100 tests and good fixture isolation, but lacks:

- coverage measurement and threshold;
- static type checks;
- property-based invariant tests;
- migration tests across every historical schema shape;
- load/concurrency tests;
- long-running runner/database growth tests;
- golden point-in-time parity tests between live and backtest pipelines.

---

## 6. Top 10 improvement opportunities

The following list is prioritized for analytical correctness and maintainability. None requires live-trading functionality.

### 1. Introduce immutable analysis snapshots and lineage

**Priority:** P0
**Why:** Prevents mixed-run regime/score/sector/capital/space state and makes every signal fully reproducible.

Recommended outcome:

- create an `analysis_runs` or `scan_snapshots` identity;
- tie regime, scores, sectors, capital, space, and signals to the same ID and candle cutoff;
- persist algorithm/config hashes;
- reject stale or cross-run dependencies rather than silently combining them.

Success criteria:

- a signal can be replayed from one immutable snapshot;
- concurrent commands cannot mix analysis artifacts;
- every report shows data cutoff and strategy version.

### 2. Establish a complete point-in-time capital-data pipeline

**Priority:** P0
**Why:** Capital Score cannot be validated historically without historical OI, funding, and ratio snapshots.

Recommended outcome:

- persist raw capital observations at regular intervals, not only derived Top20 snapshots;
- store source timestamps and endpoint status;
- enforce “available at evaluation time” reads;
- distinguish missing from neutral;
- delay Capital-related conclusions until adequate history exists.

Success criteria:

- backtest never substitutes 50 without reporting missing coverage;
- bucket summaries include sample count and coverage percentage;
- live/backtest feature parity is testable.

### 3. Replace implicit migrations with versioned migrations

**Priority:** P0
**Why:** Current table introspection/rebuild logic is increasingly risky as the schema grows.

Recommended outcome:

- add a schema version table;
- create ordered, idempotent migration scripts/functions;
- test upgrades from each released schema;
- back up/check integrity before destructive rebuilds;
- document rollback and recovery.

Success criteria:

- every database reports its schema version;
- migration history is deterministic and test-covered;
- upgrades preserve all signal/evaluation foreign keys.

### 4. Decompose the repository and CLI by bounded context

**Priority:** P1
**Why:** The 1,543-line repository and 551-line CLI are the primary maintainability bottlenecks.

Recommended outcome:

- split repositories into market data, analysis, signal/evaluation, research, paper, and operations modules;
- keep one connection/session abstraction;
- split each CLI command into a small command handler;
- introduce protocols for client/repository dependencies.

Success criteria:

- modules have narrow ownership;
- command tests no longer patch a single giant module;
- schema changes affect fewer files and tests.

### 5. Make strategy versions behaviorally complete

**Priority:** P1
**Why:** Reproducibility requires every effective decision parameter to belong to a strategy version.

Recommended outcome:

- include Capital, Space, final ranking, regime, sector, signal-window, and evaluation parameters;
- store canonical JSON plus a content hash;
- persist the exact strategy ID/hash on each score, signal, evaluation, and backtest result;
- maintain baseline compatibility tests.

Success criteria:

- two identical config hashes guarantee identical decisions on the same snapshot;
- no strategy threshold is hidden as an unversioned constant;
- candidate comparisons state the complete behavioral delta.

### 6. Add an out-of-sample research protocol

**Priority:** P1
**Why:** Current candidate ranking can overfit the available history.

Recommended outcome:

- chronological train/validation/test or walk-forward splits;
- embargo around adjacent windows;
- minimum signal count and minimum regime coverage;
- confidence intervals/bootstrap distributions;
- multiple-testing controls for candidate batches;
- candidate promotion report, never automatic activation.

Success criteria:

- candidate metrics include in-sample and out-of-sample results;
- no candidate passes with a trivial sample;
- ranking stability is measured across periods/regimes.

### 7. Add explicit data-quality and fallback telemetry

**Priority:** P1
**Why:** Neutral fallbacks currently obscure missing data.

Recommended outcome:

- persist status, source timestamp, freshness, and failure reason for Capital/Space/regime analyses;
- emit scan completeness and coverage metrics;
- make fallback policy explicit in signal JSON and backtest summaries;
- classify network, rate-limit, parse, insufficiency, and internal errors.

Success criteria:

- operators can see why any symbol lacked a feature;
- signals show whether every ranking component is observed or defaulted;
- runner health includes stale/missing analysis counts.

### 8. Build a time-ordered portfolio paper simulator

**Priority:** P2
**Why:** Independent signal outcomes do not model portfolio risk or executable performance.

Recommended outcome:

- process events chronologically;
- model overlapping positions and maximum concurrent risk;
- add fees, funding, spread/slippage assumptions, and gap handling;
- enforce sector/symbol concentration limits;
- retain the current simple ledger as a clearly labeled benchmark.

Success criteria:

- equity is derived from an explicit event ledger;
- simultaneous signals compete for finite capital;
- reported drawdown reflects portfolio chronology.

### 9. Add CI quality gates and long-horizon operational tests

**Priority:** P2
**Why:** The behavior suite is valuable but not automatically enforced by repository tooling.

Recommended outcome:

- CI for compile, unit/integration tests, Ruff, type checking, and coverage;
- minimum coverage threshold;
- migration matrix tests;
- concurrency/WAL tests;
- runner soak test with synthetic time;
- database growth and checkpoint tests.

Success criteria:

- every PR receives reproducible checks;
- type and lint regressions are blocked;
- 24/7 runner behavior is tested beyond one tick.

### 10. Reconcile roadmap, architecture, and as-built documentation

**Priority:** P2
**Why:** Current planning documents materially misrepresent repository state.

Recommended outcome:

- mark implemented roadmap items and retire obsolete milestones;
- split “as-built architecture” from “future architecture”;
- record ADRs for LONG/SHORT, Capital/Space, runner, and research safety;
- remove or explicitly defer prohibited Telegram/Web/execution goals;
- update documentation in the same PR as behavior changes.

Success criteria:

- roadmap status matches code and tests;
- architecture diagrams describe the actual runtime;
- future work is clearly separated from completed work.

---

## 7. Roadmap reconciliation

### 7.1 What is already implemented despite unchecked roadmap items

| Original milestone | Current status |
|---|---|
| M0 engineering baseline | Partially implemented: package layout, domain models, config, tests, docs; missing CI/lint/type/coverage/lockfile |
| M1 public data layer | Implemented and extended with Capital endpoints; request metrics and formal rate budget remain limited |
| M2 regime and sectors | Implemented with a different four-state naming model and JSON sector config |
| M3 score and risk levels | Implemented for LONG and SHORT, plus Capital/Space ranking |
| M4 Top3 and SQLite | Implemented and expanded to dual direction, evaluation, backtest, paper, and runner tables |
| M5 scheduler/operations | Scheduler, lock, health, and events implemented; Telegram intentionally absent |
| M6 replay/shadow readiness | Backtest, evaluation, paper, runbook, and VM guide implemented; statistical launch gates remain incomplete |

### 7.2 Roadmap elements that should be revised

- The repository is not empty.
- Telegram is no longer an active allowed scope and should be marked deferred or removed.
- Direction is no longer LONG-only.
- Capital Flow, Space, Strategy Lab, paper simulation, and Reserved VM runner are absent from the original milestone plan.
- The roadmap should distinguish “implemented”, “implemented with limitations”, “deferred”, and “not planned”.
- The original 135 unchecked items should not remain the source of truth.

---

## 8. Recommended next phase

Before adding new strategy logic, execute a stabilization phase with this sequence:

1. freeze the current baseline and compute a complete strategy/config hash;
2. add analysis-run lineage and feature availability status;
3. begin collecting raw historical capital observations;
4. introduce schema-versioned migrations;
5. split the repository and CLI by bounded context;
6. add CI/static-quality gates;
7. define out-of-sample research and minimum-sample rules;
8. rerun baseline backtests with explicit Capital/Space coverage reporting;
9. update `roadmap.md` and `architecture.md` to an as-built state;
10. only then evaluate whether any additional strategy rule is justified.

This sequencing improves confidence in the existing system without expanding into API keys, account access, order placement, Telegram, web dashboards, or automated AI trading.

---

## 9. Audit conclusion

The project has progressed far beyond its original V1 plan and already contains most of the analytical lifecycle. Its next constraint is no longer feature availability; it is **evidence quality, temporal reproducibility, schema maintainability, and operational transparency**.

The repository should therefore be treated as an early research platform rather than a validated trading system. Existing outputs are deterministic and auditable at the rule level, but the current backtest and paper results are not sufficient to establish durable statistical edge or executable profitability. Strengthening lineage, historical feature coverage, research methodology, and architecture will provide more value than adding further indicators or signal branches at this stage.
