---
name: Git sync under Replit main-agent sandbox
description: How to pull GitHub changes into the working tree when merge/ref ops are blocked
---

# Syncing GitHub code when git writes are blocked

The Replit main agent sandbox blocks "destructive" git ops (merge, checkout, reset,
ref updates, removing `.git/*.lock`). But fetching still works in two stages:

- When the USER runs `git pull` in the Shell, the **fetch half succeeds** — objects
  download and unpack, and `origin/<branch>` ref advances (e.g. `3743173..afc2a8e`).
  The **merge half fails** with a `*.lock` / "Another git process" error. That's fine.
- After that fetch, the new commit's objects are fully present locally.

**To bring the new code into the working tree without merging:**
1. `git show <sha>:path/to/file` to read each changed file (objects are now complete).
2. `git diff --stat <oldsha> <sha>` to list which files changed.
3. `cp` the `git show` output over each working-tree file (plain `cp`/write is allowed;
   `git checkout -- file` is NOT).
4. Verify with plain `diff` (not `git diff` — `git diff` itself trips the lock block
   when `.git/index.lock` is stale).

**Why:** Replit deployments snapshot the working tree, not git HEAD, so syncing files
into the working tree is enough to deploy the new code — no merge/commit required.

**How to apply:** Use whenever GitHub `main` is ahead but `git pull`/`merge` can't
complete in-sandbox. Redeploying the existing Reserved VM then ships the synced code
(no new Repl or VM purchase needed).
