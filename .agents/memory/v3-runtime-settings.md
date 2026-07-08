---
name: V3 runtime-adjustable dedup/position-limit settings
description: How dedup_hours and max_open_orders became live-adjustable via Telegram without redeploy, and where the override lives.
---

`dedup_hours` and `max_open_orders` (per strategy: v3 / v66) are no longer
baked into the pipeline at startup. A PostgreSQL table
(`v3_runtime_settings`, one row per strategy_id, NULL column = "use hardcoded
default") holds live overrides, read fresh by the scan-task closures in
`v3/runner/tasks.py` on every cycle via `V3RuntimeSettingsRepository.resolve()`.
`V3Pipeline.run()` accepts optional `dedup_hours`/`risk_config` overrides per
call so the pipeline object itself never needs to be rebuilt.

Telegram admin commands `/limits` (view) and `/setlimit <v3|v66> <dedup|maxorders> <value>`
(adjust) / `/setlimit <v3|v66> reset` read/write this table directly.

**Why:** user wanted to tune dedup window / position-limit blocking without a
redeploy cycle, since these — not stop-loss %, not the live-order-manager
code — are what actually block most repeat candidates
(`V3RiskEngine._has_open_position` / `V3DedupEngine` 24h window).

**How to apply:** any new per-strategy tunable that needs live adjustment
should follow this same pattern — add a nullable column to
`v3_runtime_settings` (or a new settings table if unrelated), resolve it at
the top of the relevant scan-task closure, never assume the value captured at
`build_v3_tasks()`/`build_v66_tasks()` time is still current 15 minutes later.
`dedup_hours` is constrained to `{4, 12, 24, 48}` (same set `V3DedupEngine`
itself accepts).
