# P0-3 Data Quality Status — Implementation Report

## Audit findings

The runtime currently exposes collection `SUCCEEDED/PARTIAL/FAILED`, but derived artifacts do not share a quality contract. Missing Capital and Space data silently become neutral `50`; missing sector snapshots silently disable the sector gate; insufficient regime history becomes `OBSERVE`; and backtest results do not disclose which fallback inputs affected final ranking.

## Contract

Every persisted scan, score, regime, sector, capital, space, signal, and backtest result will expose one of:

- `COMPLETE`: all required inputs were present and current;
- `PARTIAL`: usable output was produced from an incomplete scan or incomplete constituent set;
- `STALE`: source observations existed but exceeded the accepted freshness window;
- `FALLBACK`: a documented neutral/default value was substituted;
- `MISSING`: required inputs were absent and no usable artifact was produced.

Precedence for aggregate status is `MISSING > FALLBACK > STALE > PARTIAL > COMPLETE`.

## Non-strategy design

Quality is metadata only. Existing score values, gates, rankings, entries, stops, targets, RR, and fallback numeric values remain unchanged. Signals and backtest results additionally persist a JSON quality context naming the scan, score, regime, sector, capital, and space statuses so fallback inputs cannot remain silent.

## 1. Current state before P0-3

Before this change, quality semantics were fragmented:

- `collection_runs.status` could report `PARTIAL`, but downstream records did not retain that fact;
- valid scores were indistinguishable whether their scan was complete or had interval failures;
- insufficient regime history returned `OBSERVE` without identifying missing input data;
- missing sector snapshots disabled sector filtering without an explicit output flag;
- missing or stale Capital Flow inputs became a neutral score of 50;
- insufficient Space history became a neutral score of 50;
- signals persisted only the resulting numeric values;
- backtest rows did not record whether Capital or Space values were reconstructed or substituted.

This meant strategy behavior was deterministic but input degradation was not directly auditable from the resulting signal or backtest row.

## 2. Design decisions

### 2.1 Shared status vocabulary

`src/binance_ai_trader/data_quality.py` defines and validates the only permitted statuses. Aggregate status uses a deterministic worst-status calculation:

```text
COMPLETE < PARTIAL < STALE < FALLBACK < MISSING
```

The aggregate does not change any decision. It describes the strongest quality warning present in its context.

### 2.2 Artifact-level status

A `data_quality_status` field is attached to domain and persisted records for:

- scan/collection runs;
- symbol scores;
- market regime;
- sector snapshots;
- Capital Flow snapshots;
- Space snapshots;
- signals;
- backtest results.

Signals and backtest results additionally persist `data_quality_json`, a component-level context. Example:

```json
{
  "scan": "COMPLETE",
  "score": "COMPLETE",
  "regime": "COMPLETE",
  "sector": "MISSING",
  "sector_value": "FALLBACK",
  "capital": "STALE",
  "capital_value": "FALLBACK",
  "space": "MISSING",
  "space_value": "FALLBACK"
}
```

This explicitly separates the source condition (`STALE` or `MISSING`) from the fact that an existing neutral value was substituted (`FALLBACK`).

### 2.3 Metadata-only propagation

No status blocks or admits a candidate. Existing fallback values remain 50, missing-sector behavior remains unchanged, and regime/scoring/signal rules remain unchanged. Quality metadata is calculated after or alongside the existing numerical path and is persisted for inspection.

## 3. Status derivation

### Scan

- successful collection with no interval failures: `COMPLETE`;
- isolated interval/symbol failures: `PARTIAL`;
- failed run: `MISSING`.

### Score

- complete scan and all requested symbols scored: `COMPLETE`;
- partial source scan or insufficient-history symbols: `PARTIAL`;
- policy-excluded symbols remain listed as skipped but do not degrade quality;
- no score row is generated when scoring cannot be performed.

### Regime

- BTC and ETH have all required closed candles: source scan quality, or `COMPLETE` for standalone manual analysis;
- some required candles exist but the full requirement is not met: `PARTIAL`;
- no required market data exists: `MISSING`.

The state remains `OBSERVE` under existing regime rules; quality merely explains why.

### Sector

Each sector receives the worst status of its member scores. A missing sector row remains missing. If Signal Generation uses the existing no-snapshot fallback behavior, the signal context records both `sector=MISSING` and `sector_value=FALLBACK`.

### Capital Flow

- all timestamped OI, funding, ratio, and volume inputs satisfy freshness limits: `COMPLETE`;
- only part of the required observation set exists: `PARTIAL`;
- all required metrics exist but one or more are older than their accepted freshness window: `STALE`;
- no historical observations exist: `MISSING`.

When no Capital snapshot can be reconstructed and the existing neutral 50 is used, the signal/backtest context records `capital_value=FALLBACK` without hiding the underlying `PARTIAL`, `STALE`, or `MISSING` condition.

### Space

A full 720-bar closed 4h window remains `COMPLETE`. If the existing Space calculation cannot run, no Space snapshot is created; signals and backtests expose `space=MISSING` and `space_value=FALLBACK` when neutral 50 is used.

### Signal

Signals aggregate scan, score, regime, sector, Capital, and Space status. The complete component map is persisted as JSON and emitted by the scan CLI alongside the aggregate status.

### Backtest result

Each backtest result stores the same component-level context and aggregate status. Point-in-time Capital freshness and Space availability are calculated at the historical evaluation timestamp, so fallback use is visible per result.

## 4. SQLite migration

The migration is additive and backward compatible:

1. adds `data_quality_status` to `collection_runs`, `scores`, `capital_snapshots`, `space_snapshots`, `signals`, `market_regimes`, `sector_snapshots`, and `backtest_results` when absent;
2. adds `data_quality_json` to `signals` and `backtest_results` when absent;
3. maps historical collection status deterministically:
   - `SUCCEEDED` → `COMPLETE`;
   - `PARTIAL` → `PARTIAL`;
   - other states → `MISSING`;
4. defaults existing derived rows to `COMPLETE` because their precise historical degradation cannot be inferred safely;
5. installs insert/update triggers that reject any status outside the five-value contract.

No existing table is dropped and no existing strategy result is recalculated.

## 5. Output visibility

The CLI now includes quality status in:

- signal JSON Lines, including full `data_quality` context;
- standalone regime JSON;
- sector JSON Lines;
- Capital and Space output through their dataclass JSON serialization.

Scores, scan runs, and backtest results expose status in SQLite. Backtest result context is retained per historical signal rather than collapsed into a potentially misleading run-wide value.

## 6. Validation results

Focused tests prove:

1. a `PARTIAL` collection run propagates into score objects and persisted score rows;
2. stale Capital observations are identified as `STALE`;
3. the unchanged neutral Capital value of 50 is accompanied by `capital_value=FALLBACK` on the signal;
4. missing Space data is accompanied by `space=MISSING` and `space_value=FALLBACK`;
5. signal aggregate status and full JSON context are persisted exactly;
6. all required runtime tables expose `data_quality_status`;
7. Capital, Space, and backtest rows preserve explicit non-complete statuses;
8. the shared precedence helper is deterministic and rejects unknown statuses;
9. the complete 120-test unit/integration suite and smoke sequence remain green.

## 7. Remaining limitations

1. Historical derived rows default to `COMPLETE` because old databases did not retain enough evidence to infer prior fallback use.
2. No separate row is created for a score, sector, Capital, or Space artifact that was never produced. Its absence is represented as `MISSING` in consuming signal/backtest contexts.
3. Data quality does not currently alter eligibility or ranking; that is intentional to satisfy the no-strategy-change constraint.
4. A later reporting phase may add quality coverage percentages and summaries by run, symbol, and endpoint.
5. Capital freshness thresholds remain those introduced by P0-2; P0-3 only exposes their result.

## 8. Safety confirmation

P0-3 changes metadata and observability only. It does not modify:

- scoring formulas or weights;
- Capital or Space formulas;
- final signal ranking;
- Regime or Sector gate rules;
- LONG/SHORT construction;
- Entry, Stop Loss, TP1, TP2, or RR;
- evaluation or paper-account outcomes.

No API Key, exchange account interface, order endpoint, live trading, Telegram, Web dashboard, or AI execution behavior is added.
