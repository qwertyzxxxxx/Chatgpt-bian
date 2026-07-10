---
name: New paper-only strategy checklist
description: Checklist for wiring a new V3-pattern strategy into this bot so it actually runs in production, not just in local CLI runs.
---

This project has two separate places task lists get built:
- `run_server.py` — the actual production entrypoint (invoked by the Reserved VM). Builds `tasks` directly by calling `build_*_tasks()` functions and gating them on env vars (e.g. `LIVE_TRADING_ENABLED`).
- `src/binance_ai_trader/entrypoints/cli.py` — a separate CLI entrypoint used for local/manual runs, gated on argparse flags.

**Why:** these two entrypoints do not share task-building logic or gating flags. A new strategy wired only into `cli.py` (or only into `run_server.py`) will silently never run in the other context. Early in this project it was easy to add a new strategy's `build_X_tasks()` call to `cli.py` and forget `run_server.py`, which is the one that matters for actual paper/live testing on the Reserved VM.

**How to apply:** when adding a new paper-only or live strategy:
1. Add a `build_<strategy>_tasks()` in `v3/runner/tasks.py`.
2. Wire it into `run_server.py` behind an explicit env var (e.g. `ENABLE_<STRATEGY>=true`), appended to `tasks` before `ProductionRunner` is constructed.
3. Optionally also wire an argparse flag into `cli.py` for local testing, but treat `run_server.py` wiring as the one that determines prod behavior.
4. Confirm with the user before flipping the env var on in the actual deployed/Reserved-VM environment — new strategies should stay off until explicitly approved.
