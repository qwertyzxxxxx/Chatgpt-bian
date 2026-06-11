# P0-2 Historical Capital Flow Coverage Report

**Date:** 2026-06-07

## Executive summary

P0-2 replaces the backtest's dependence on previously derived live Capital Scores with reconstructable, timestamped raw public-market observations. Live scans and historical backtests now call the same `CapitalFlowHistory.score_at` path, which reconstructs `CapitalInputs` at an explicit cutoff and invokes the unchanged `CapitalFlowEngine`.

The implementation remains read-only. It adds no exchange credentials, account access, order endpoint, live execution, Telegram, Web, strategy-rule, scoring-weight, signal-ranking, or LONG/SHORT behavior.

## 1. Current state before P0-2

### 1.1 Live path

Before this change, `CapitalFlowAnalyzer`:

1. selected the latest Top-20 scores;
2. fetched a short Open Interest history;
3. fetched current Open Interest, current funding, and one current global long/short ratio;
4. calculated `CapitalSnapshot` immediately;
5. persisted only the derived fields in `capital_snapshots`.

Raw source timestamps and raw observations were discarded. The resulting score could be displayed, but it could not be reconstructed independently from SQLite.

### 1.2 Backtest path

The backtest did not execute `CapitalFlowEngine`. It called `load_capital_score_at`, which selected the most recent derived `capital_snapshots` row whose `calculated_at` was before the backtest point.

That behavior had two important approximations:

- a score calculated for a different scan could be reused indefinitely even when its OI, funding, ratio, or volume inputs were stale;
- if no prior derived row existed, the backtest silently used neutral `50.0`.

Consequently, live and backtest paths were not equivalent, and a Capital Score in a historical result did not prove that the required raw data existed at that timestamp.

### 1.3 Point-in-time volume gap

The previous average-volume query read the latest stored 15m bars without a historical cutoff. It was safe for a current live calculation but not reusable for historical reconstruction because future bars could enter the average.

### 1.4 Public API history constraints

The implementation uses only public Binance USD-M market-data endpoints:

- Open Interest Statistics: `/futures/data/openInterestHist`;
- Funding Rate History: `/fapi/v1/fundingRate`;
- Global Long/Short Account Ratio: `/futures/data/globalLongShortAccountRatio`.

The OI and ratio APIs expose timestamps and support bounded `startTime`/`endTime` queries. Binance currently documents approximately one month of OI/ratio availability. Funding history has a larger limit but is collected over the same bounded window for consistent ingestion.

## 2. Gaps found

The audit identified the following concrete gaps:

1. no raw Capital Flow observation table;
2. no persisted source timestamp for OI, funding, or ratio;
3. no historical funding or ratio client methods;
4. positional OI lookbacks rather than timestamp-based point-in-time selection;
5. no shared live/backtest reconstruction path;
6. backtests reused potentially stale derived scores;
7. missing data was indistinguishable from a true score of 50;
8. average quote volume was not filtered by the historical cutoff;
9. derived `capital_snapshots` had only indirect `run_id` lineage;
10. old databases had no migration path for raw historical observations.

## 3. Pre-implementation plan

The implementation plan was written before changing Capital Flow logic:

1. persist append-only raw observations by symbol, metric, and source timestamp;
2. link each observation to the immutable snapshot that ingested it;
3. preserve timestamps in public-client history methods;
4. ingest all available OI, funding, and ratio history for each analyzed candidate;
5. reconstruct inputs independently at `T`, `T-1h`, `T-4h`, and `T-24h`;
6. reject observations newer than the requested cutoff;
7. use one shared history service for live and backtest calculation;
8. add direct snapshot lineage to derived Capital snapshots;
9. preserve existing databases and legacy reads;
10. validate reconstruction, future exclusion, parity, lineage, and migration.

## 4. Design decisions

### 4.1 Raw observation model

The new `capital_flow_observations` table stores one immutable row per:

```text
(symbol, metric, observed_at_ms)
```

Supported metrics are:

- `OPEN_INTEREST`;
- `FUNDING_RATE`;
- `LONG_SHORT_RATIO`;
- `QUOTE_VOLUME_24H`.

Each row also stores:

- the decimal value as text;
- `ingested_snapshot_id`;
- local `captured_at` time.

The primary key makes repeated history ingestion idempotent. `INSERT OR IGNORE` preserves the first ingestion lineage, while database triggers reject updates and deletes.

### 4.2 Historical ingestion

For each analyzed Top-20 symbol, the live analyzer:

1. requests a bounded 30-day OI history;
2. requests a bounded 30-day global long/short ratio history;
3. requests bounded funding history;
4. paginates when a response reaches the endpoint limit;
5. persists every timestamped observation;
6. persists the exact scan ticker `quote_volume_24h` at the immutable scan cutoff.

Responses with timestamps after the snapshot cutoff may be stored for future analysis but are never selected for the current snapshot.

### 4.3 Point-in-time reconstruction

`load_capital_inputs_at(symbol, T)` selects:

- latest OI at or before `T`;
- latest OI at or before `T-1h`;
- latest OI at or before `T-4h`;
- latest OI at or before `T-24h`;
- latest funding at or before `T`;
- latest ratio at or before `T`;
- latest captured 24h quote volume at or before `T`.

Freshness bounds prevent an arbitrarily old observation from masquerading as current:

- OI and ratio: maximum two hours old;
- funding: maximum twelve hours old;
- captured 24h quote volume: maximum two hours old.

If captured quote volume is unavailable, the repository reconstructs rolling 24h quote volume from the latest 96 already-closed 15m bars at or before `T`. Average daily volume uses no more than the latest 672 closed 15m bars at or before `T`. No bar after `T` participates.

### 4.4 Shared calculation path

`CapitalFlowHistory.score_at(run_id, symbol, T)` is now the only new point-in-time calculation path:

1. repository reconstructs `CapitalInputs`;
2. unchanged `CapitalFlowEngine.score` calculates all component fields and final score.

Both `CapitalFlowAnalyzer` and `BacktestEngine` use this service. The Capital formula and its existing 30% volume, 35% OI, 20% funding, and 15% crowding weights were not changed.

### 4.5 Missing historical coverage

When complete point-in-time inputs are unavailable, `score_at` returns no snapshot. Backtest retains neutral `50.0` only as a backward-compatible strategy fallback. It no longer reuses an unrelated or arbitrarily stale derived Capital Score.

This distinction is important: P0-2 makes data collected from now on reconstructable and backfills the exchange's available public-history window, but it cannot manufacture OI or ratio data older than the exchange retention period.

## 5. Snapshot lineage

P0-1 lineage remains intact:

- every raw observation references the immutable snapshot that ingested it;
- `capital_snapshots` now contains `snapshot_id` directly;
- a database trigger rejects a derived Capital snapshot whose snapshot does not belong to its collection run;
- repository writes reject finalized ingestion snapshots;
- backtest results retain their own point-in-time `BACKTEST` snapshot while the raw source observations retain ingestion lineage.

This separates two audit questions cleanly:

1. **When did the market metric occur?** `observed_at_ms`.
2. **Which immutable run imported it?** `ingested_snapshot_id`.

## 6. Migration details

### 6.1 Fresh databases

Fresh databases create:

- `capital_flow_observations` with its primary key, metric check, snapshot foreign key, and lookup index;
- `capital_snapshots.snapshot_id` as a required foreign key;
- immutability and lineage triggers.

### 6.2 Existing databases

Existing databases are upgraded in place:

1. `capital_flow_observations` is created without changing existing data;
2. nullable `snapshot_id` is added to legacy `capital_snapshots` because SQLite cannot safely add a new required foreign key to populated tables;
3. existing derived rows are backfilled through deterministic `snapshot-<run_id>` scan snapshots created by P0-1;
4. an index is created for derived snapshot lookups;
5. all new derived inserts must pass the run/snapshot consistency trigger.

Legacy derived scores remain readable. They are not converted into raw observations because their original OI/funding/ratio inputs and timestamps cannot be recovered reliably.

## 7. Validation results

Automated validation covers the required invariants:

### 7.1 Historical reconstruction

A fixture persists timestamped OI at `T`, `T-1h`, `T-4h`, and `T-24h`, plus funding, ratio, and volume. The shared history service reconstructs the expected 24-hour OI change and a deterministic Capital Score.

### 7.2 No future leakage

Extreme OI, funding, ratio, and volume observations are inserted at `T+1h`. Recalculating at `T` returns an identical Capital snapshot, proving all raw queries enforce `observed_at_ms <= T`.

### 7.3 Live/backtest equivalence

The live analyzer ingests a fake public history and calculates its derived snapshot. The same SQLite observations are replayed at the same timestamp through `CapitalFlowHistory`; component values and final Capital Score match exactly. The backtest engine now calls this same service.

### 7.4 Snapshot lineage

Tests verify that all raw observations and the derived live Capital snapshot reference the originating scan snapshot.

### 7.5 Backward-compatible migration

A database fixture with the legacy `capital_snapshots` shape is opened by the new repository. The migration creates the raw observation table, adds `snapshot_id`, and links the existing row to `snapshot-legacy` without deleting the derived score.

### 7.6 Regression coverage

The complete project test suite is run after the focused Capital Flow tests, including scan, signals, evaluation, backtest, strategy lab, paper simulation, runner, and P0-1 snapshot lineage tests.

## 8. Remaining limitations

1. Binance currently limits OI and global ratio history to roughly the latest month; data older than that cannot be retroactively reconstructed from Binance public endpoints.
2. Historical completeness grows from persisted observations. A database that never ran the collector has no historical OI/ratio coverage beyond what can still be backfilled from the exchange.
3. The analyzer continues to collect Capital Flow for the scored Top-20 candidates, not every listed perpetual contract, to remain consistent with the existing pipeline and public endpoint limits.
4. Missing complete inputs still map to neutral 50 in final ranking for backward strategy compatibility. A later data-quality phase should persist an explicit `AVAILABLE`, `STALE`, or `MISSING` state and expose coverage percentages.
5. Historical ticker quote volume is exact only when a scan captured it. Otherwise it is reconstructed from stored 15m quote-volume bars.
6. Existing legacy derived scores are retained but cannot be transformed into raw history without inventing source data.
7. Public-history availability and rate limits are external exchange constraints and may change.

## 9. Safety confirmation

P0-2 does not change:

- Capital Flow formula or component weights;
- scoring weights;
- final signal-ranking weights;
- Regime or Sector gates;
- LONG/SHORT selection rules;
- Entry, Stop Loss, TP1, TP2, or RR rules;
- signal evaluation semantics.

It adds no API key, account endpoint, order endpoint, live-trading functionality, Telegram behavior, Web dashboard, or AI execution behavior.
