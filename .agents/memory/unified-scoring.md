---
name: Unified scoring SCORE_V1_UNIFIED
description: Architecture of the 100pt unified signal scoring system — module layout, C1-4 adapter mapping, V3 wiring, failure safety.
---

## Module layout
`src/binance_ai_trader/v3/scoring/`
- `models.py` — `UnifiedScore` dataclass, `score_grade()`, `rr_score_pts()`
- `engine.py` — `score_signal(candidate, klines)` + `score_signal_with_client(candidate, client)` + per-strategy `_strategy_fit_score()` adapters
- `formatter.py` — `format_score_block(score)` → Telegram string
- `__init__.py` — re-exports public API

## 5 categories (max)
  volume_score          0-30  (quote_volume + M15 vol_ratio + H1 continuity + bar quality)
  trend_structure_score 0-25  (D1 EMA50 + H4 triple EMA + H1 structure + M15 bar)
  entry_position_score  0-20  (H1 + M15 dist from struct high/low in ATR units)
  risk_reward_score     0-15  (stop quality 0-7 + RR quality 0-8 via rr_score_pts())
  strategy_fit_score    0-10  (per-adapter: V3/V66/V662/V663/V664/wave/classic)

Grades: A≥85  B≥70  C≥55  D<55. score_version="SCORE_V1_UNIFIED"

## V664 volume inversion
V664 is a pullback strategy — low M15 vol_ratio is ideal.
`_volume_score()` detects `"v664" in sid` and inverts M15 vol scoring:
  < 0.6x → 10pts  (ideal contraction)
  ≥ 1.2x → 2pts   (unexpected volume = weaker)

## C1-4 adapter mapping (ONE score, not two)
C1-4 still call `compute_score()` → `ScoreBreakdown(time_space, trend, pattern, volume)`.
`"score_breakdown": sb` is added to each strategy's return dict.
`telegram_push.py::_unified_from_classic(sig)` maps it:
  volume_score          = min(30, round(sb.volume × 1.5))      # 20→30
  trend_structure_score = sb.trend                              # 25→25 (same)
  entry_position_score  = min(20, round(sb.time_space × 2/3))  # 30→20
  risk_reward_score     = rr_score_pts(stop_pct, rr)           # NEW 0-15
  strategy_fit_score    = min(10, round(sb.pattern × 0.4))     # 25→10
The old `score` field in ClassicSignal still shows in TG header; unified block appended below.

## V3 wiring
`V3TelegramNotifier.send_candidate(candidate, hold_hours, live_prefix, client=None)`
  → if client provided: calls `score_signal_with_client(candidate, client)`
    → fetches klines 1d[51][:-1], 4h[61][:-1], 1h[61][:-1], 15m[41][:-1]
    → runs score_signal()
    → saves to DB via UPDATE v3_candidates SET score_* WHERE signal_id
    → returns UnifiedScore | None
  → appends format_score_block(score) to message
tasks.py passes `client=client` to all 8 send_candidate() calls.

## DB schema (v3_candidates)
11 new columns added via ALTER TABLE ... ADD COLUMN IF NOT EXISTS in init_schema():
  score_total, score_grade, score_version,
  volume_score, trend_structure_score, entry_position_score, risk_reward_score,
  strategy_fit_score, score_summary, score_details_json, scored_at

## Failure safety
- Entire scoring wrapped in try/except in score_signal_with_client()
- Returns None on any failure → message sent without score block
- _SCORING_AVAILABLE module-level flag in notifier.py + telegram_push.py
  guards all scoring imports; bot starts normally even if scoring import fails

**Why:**
User requirement: "评分失败时不得中断信号推送". Scoring is best-effort only.
