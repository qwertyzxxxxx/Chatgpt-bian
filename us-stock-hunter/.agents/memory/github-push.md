---
name: GitHub push blocked
description: Why git push to qwertyzxxxxx/us-stock-hunter fails in Replit environment
---

## Situation
`git remote -v` shows `origin https://github.com/qwertyzxxxxx/us-stock-hunter`
`git push -u origin main` times out — no PAT configured in the environment.

## Fix options
1. Set credential in remote URL:
   `git remote set-url origin https://<TOKEN>@github.com/qwertyzxxxxx/us-stock-hunter.git`
2. Use Replit Git sidebar (left panel → Git icon → connect GitHub)
3. User must supply the PAT; we cannot store it as an env secret (security boundary)

**Why:** Replit sandbox has no stored GitHub credentials; HTTPS push blocks waiting for stdin auth.
