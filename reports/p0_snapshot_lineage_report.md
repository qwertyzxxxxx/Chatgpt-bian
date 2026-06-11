# P0-1 Immutable Analysis Snapshot Lineage Report

**Date:** 2026-06-07
**Scope:** Runtime lineage only; no strategy, scoring, ranking, entry, stop, target, or execution-rule changes.

## 1. Problem statement

The project audit identified a P0 temporal-consistency risk: scan orchestration previously selected several artifacts through independent “latest” queries. Scores and sector/capital/space records were associated with a collection `run_id`, while market regime was effectively global. Concurrent commands or a manually repeated analysis could therefore cause signal generation to combine records produced at different cutoffs.

Evaluation records identified their source signal only by `(signal_run_id, symbol)`, and backtest records identified an evaluation point only by `(backtest_run_id, evaluation_time_ms)`. Those keys were useful, but there was no single first-class immutable object that represented the complete information boundary used by a decision.

## 2. Repository and schema review

The existing SQLite repository stores:

- raw/runtime data in `collection_runs`, `universe_snapshots`, and `klines`;
- derived scan artifacts in `scores`, `market_regimes`, `sector_snapshots`, `capital_snapshots`, and `space_snapshots`;
- decisions and outcomes in `signals` and `signal_evaluations`;
- historical replay in `backtest_runs` and `backtest_results`;
- strategy research, paper-account, reporting, and runner state in their existing tables.

The scan-derived tables already use `run_id` except `market_regimes`. Because one collection run now has exactly one scan snapshot, those existing `run_id` relationships remain the minimal lineage mechanism for scores, sectors, capital, and space. Adding a duplicate `snapshot_id` to every one of those tables would increase migration and consistency burden without adding information.

## 3. Minimal design

### 3.1 `analysis_snapshots`

The migration adds one root lineage table:

| Column | Purpose |
| --- | --- |
| `snapshot_id` | Stable primary identifier used by decisions and outcomes |
| `snapshot_type` | `SCAN`, `BACKTEST`, or `MANUAL` |
| `collection_run_id` | Unique scan-run link for live/read-only scan snapshots |
| `source_ref` | Stable source identity (`run_id`, backtest point, or manual analysis reference) |
| `data_cutoff_ms` | Latest closed-market-data timestamp allowed in analysis |
| `strategy_id` | Strategy identity used by the snapshot; currently `baseline_v1` |
| `created_at` | Snapshot creation time |
| `finalized_at` | Finalization boundary after which repository writes are rejected |

Uniqueness constraints ensure one scan snapshot per collection run and one snapshot per `(snapshot_type, source_ref)`.

### 3.2 Where `snapshot_id` is stored

A direct `snapshot_id` was added only where a durable decision or result needs an unambiguous lineage root:

- `signals`: identifies the immutable scan snapshot that produced the signal;
- `signal_evaluations`: carries the same snapshot as the source signal, preventing cross-run outcome attachment;
- `backtest_results`: identifies the point-in-time backtest snapshot used for that result;
- `market_regimes`: closes the previous global-regime lineage gap.

The existing `run_id` remains sufficient for `scores`, `sector_snapshots`, `capital_snapshots`, and `space_snapshots`, because `analysis_snapshots.collection_run_id` is unique. This keeps the migration intentionally small.

## 4. Runtime behavior

### 4.1 Scan

1. `start_run` atomically creates the `collection_runs` record and exactly one `SCAN` snapshot.
2. The snapshot cutoff is fixed from the run start time.
3. Regime, scoring, sector, capital, space, and signal application services receive the same `snapshot_id`.
4. Time-series reads use `data_cutoff_ms` where point-in-time filtering is required.
5. Signal persistence verifies that the snapshot belongs to the signal's collection run.
6. Signal generation finalizes the snapshot after the signal set is persisted, including an empty signal set.

The CLI includes `snapshot_id` in emitted signal JSON, making the decision lineage visible to downstream operators.

### 4.2 Evaluation

Evaluation retains the source signal's snapshot rather than creating a new market-analysis snapshot. This reflects the actual lineage: future bars determine the outcome, but the decision being evaluated was made from the original immutable scan snapshot.

SQLite triggers reject an insert or update when the evaluation's `snapshot_id` does not match the referenced `(signal_run_id, symbol)` signal. Repository fallback resolution also derives a missing evaluation snapshot directly from the source signal for compatibility with existing callers.

### 4.3 Backtest

Each historical evaluation timestamp creates one finalized `BACKTEST` snapshot with:

- `source_ref = <backtest_run_id>:<evaluation_time_ms>`;
- `data_cutoff_ms = evaluation_time_ms`.

Every result generated at that point references the same snapshot. SQLite rejects a result whose snapshot type, run, or evaluation time does not match the backtest point.

### 4.4 Standalone regime analysis

A standalone `regime` command creates and finalizes a `MANUAL` snapshot. It does not overwrite or append to a finalized scan snapshot. The scan pipeline passes its explicit scan snapshot and is unaffected.

## 5. Immutability and consistency controls

The implementation provides layered controls:

- database uniqueness: one scan snapshot per collection run;
- database foreign keys: decisions/outcomes must reference an existing snapshot;
- database triggers: snapshot deletion is forbidden, finalized metadata cannot change, and signal/evaluation/backtest lineage must match;
- repository guards: derived scan writes reject finalized snapshots;
- snapshot-specific reads: signal generation no longer combines independently selected latest artifacts;
- deterministic backfill: historical collection runs receive `snapshot-<run_id>` identities, existing signals/evaluations/regimes are linked, and historical backtest points receive deterministic snapshot IDs.

## 6. Compatibility and migration behavior

Fresh databases create the new schema directly. Existing databases are migrated in place:

1. nullable `snapshot_id` columns are added where absent;
2. scan snapshots are backfilled from `collection_runs`;
3. signals are linked by `run_id`;
4. evaluations are linked through their source signals;
5. regimes are linked to the latest eligible historical scan timestamp where possible;
6. backtest snapshots are created from distinct persisted evaluation points;
7. indexes and consistency triggers are installed.

Nullable migration columns are retained for legacy rows that cannot be safely inferred. New runtime writes are guarded and always supply valid lineage.

## 7. Validation

The dedicated integration suite proves:

- one and only one scan snapshot is created for each collection run;
- persisted signals contain the scan snapshot ID;
- an evaluation cannot use another run's snapshot and a missing snapshot is resolved from the matching signal;
- backtest results join to the correct point-in-time backtest snapshot.

The existing full test suite also verifies that collection, regime, scoring, sector/capital/space analysis, LONG/SHORT signal generation, evaluation, backtest, strategy research, paper simulation, health, and runner behavior remain unchanged.

## 8. Safety and non-goals

This change does **not**:

- change any strategy rule or scoring weight;
- change signal ranking, LONG/SHORT selection, Entry, Stop Loss, TP, or RR logic;
- add API keys, account endpoints, order endpoints, or live trading;
- add Telegram or Web behavior;
- claim that snapshot lineage alone makes historical capital data complete.

## 9. Remaining follow-up work

The P0 lineage root is intentionally minimal. Recommended later work includes:

1. store a cryptographic hash of the complete effective strategy/configuration artifact;
2. introduce ordered schema-version migrations instead of implicit shape detection;
3. add explicit feature availability and stale/missing reasons for capital and space data;
4. expose snapshot lookup/report commands for operator replay;
5. add migration fixtures covering every historical schema version;
6. consider database-level write guards for every derived table after finalization, not only repository-level guards.

These are separate hardening tasks and were not bundled into this P0 implementation.
