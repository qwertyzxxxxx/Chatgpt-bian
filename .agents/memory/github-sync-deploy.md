---
name: GitHub → production sync & deploy
description: How this production Replit pulls dev's GitHub changes and what local-only files must survive the overwrite
---

This Replit is the **production** node: it deploys to a Reserved VM and pulls code from
GitHub manually. A separate **dev** Replit pushes to GitHub (repo: `qwertyzxxxxx/Chatgpt-bian`).

## Sync procedure
- Download a snapshot zip (git pull/merge is blocked in the sandbox):
  `curl -sL https://github.com/qwertyzxxxxx/Chatgpt-bian/archive/refs/heads/main.zip -o /tmp/gh.zip`
  then unzip to `/tmp/gh_latest/Chatgpt-bian-main/`.
- `diff -rq src/ <GH>/src/` to see what dev changed, then `cp -r <GH>/src/binance_ai_trader/* src/binance_ai_trader/`.
- Deploy snapshots the working tree, so a plain copy + Publish is enough.

## Local-only files that are NOT in GitHub — never overwrite, always preserve
- `run_server.py` — the port-8080 health wrapper + production launch args (run-loop flags). GitHub has no copy.
- `pyproject.toml` — local has a `[tool.uv]\npackage = false` block GitHub lacks.

**Why:** copying the GitHub tree wholesale would delete/clobber these and break the deploy.
**How to apply:** copy only `src/` from GitHub; verify `run_server.py` and `pyproject.toml` are untouched afterward.

## Verifying a sync before Publish
- Confirm both dev's fix AND any local fixes survived. Dev usually merges your prior local fixes
  back through GitHub, so a `diff` showing only additions (no deletions of your code) means your
  fix is preserved. Always grep for your fix markers after copying.
- `python3 -m py_compile <changed files>` + an import smoke test + run the changed entrypoint
  functionally against `data/*.db` before suggesting deploy.
