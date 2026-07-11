---
name: V662 strategy and V66 TP fix
description: V662 is a V66 upgrade with volume ratio + 1h/4h trend gates. V66 live TP was also fixed from TP1 to TP2.
---

## V66 live TP bug fix
`v3/live/engine.py` previously used `candidate.tp1` (1:1 RR) for the live OTOCO bracket. Fixed to `candidate.tp2 if candidate.tp2 else candidate.tp1` (2:1 RR). Two locations: `try_place()` line ~121 and `_save_status_order()` line ~820.

**Why:** With 55% win rate and 1:1 RR effective, edge was only +0.10R before fees → net loss. TP2 target (2:1 RR) restores the mathematical edge.

## V662 — what changed vs V66

| Param | V66 | V662 |
|-------|-----|------|
| min_move_pct | 0% | 5% |
| max_stop_pct | 5% | 3% |
| min_volume_ratio | none | 1.2x |
| require_trend_aligned_1h | False | True |
| require_trend_aligned_4h | False | True |
| max_ttl_minutes | 120 | 90 |

## Architecture changes for V662
- `hotlist/models.py`: added `trend_4h_aligned: bool = True` to `HotlistEntryPlan`
- `hotlist/service.py`: `_plan()` and `plan_candidate()` accept optional `fourh` klines; computes EMA50 on 4h; sets `trend_4h_aligned`
- `hotlist/watchlist.py`: `HotlistWatchlistPolicy` has new fields `min_move_pct`, `min_volume_ratio`, `require_trend_aligned_1h`, `require_trend_aligned_4h` (all default to off — backward compatible with V66)
- `v3/strategies/v662.py`: new strategy file using the above policy
- `v3/runner/tasks.py`: `build_v662_tasks()` added, paper-only
- `run_server.py`: gated behind `ENABLE_V662=true` (already set)

## V663 — 三线排列升级版（EMA10>20>50）

V663 = V662 基础上把趋势判断从"价格在EMA某侧"改为"三线排列"：
- 1h: EMA10 > EMA20 > EMA50（多头）/ 反向（空头）
- 4h: 同上
- Policy 字段: `require_triple_ema_1h=True`, `require_triple_ema_4h=True`
- 注意: 1h 抓取从30条改为60条（全局），以支持 EMA50 计算

**Why:** 三线排列比单纯价格位置更强的趋势确认，过滤震荡行情假突破。

## How to apply for future new strategies
When adding gates to `HotlistWatchlistPolicy`, use default values that preserve existing V66 behavior (0 / False). Gate logic lives in `HotlistWatchlist.review()`. 4h data fetch is lazy — only fetched when `require_trend_aligned_4h=True`.
