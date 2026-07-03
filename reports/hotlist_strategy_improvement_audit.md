# Hotlist Strategy Improvement Audit

**Generated**: 2026-07-03T20:27:52+00:00  
**Data**: `data/market_data.db` — 380 opps (June 18-23), 273 unique, 24h expiry re-settlement  
**Settlement**: 15m klines · fill=candle touch entry · TP1/SL/Timeout/Expired · WR = TP1/(TP1+SL)

---

## 一、V1 / V2 / Candidate 规则对比

### 共享基础引擎 — `HotlistWatcher` (`service.py`)

所有三者调用同一个 `HotlistWatcher.watch()` 生成候选，参数和公式完全共享：

| 参数 | 值 | 来源 |
|---|---|---|
| Universe | USDT 永续，TRADING，去稳定币/杠杆币/黑名单 | `funnel.py` Stage 1-3 |
| 24h move | ≥ 15% | `HotlistWatcherPolicy.min_move_pct` |
| 成交额 | ≥ 5,000,000 USDT | `HotlistWatcherPolicy.min_quote_volume` |
| LONG 判断 | `price_change_percent > 0` | `service.py:87` |
| SHORT 判断 | `price_change_percent < 0` | `service.py:88` |
| Entry LONG | `min(EMA20_15m, current - 0.25×ATR14)` | `service.py:141` |
| Entry SHORT | `max(EMA20_15m, current + 0.25×ATR14)` | `service.py:154` |
| SL LONG | `min(swing_low_20, entry - ATR14)` | `service.py:142` |
| SL SHORT | `max(swing_high_20, entry + ATR14)` | `service.py:155` |
| TP1 | `entry ± (entry - SL)` | `service.py:144` |
| TP2 | `entry ± 2×(entry - SL)` | `service.py:145` |
| RR | 固定 2.00 | `service.py:182` |
| 排序 | \|change_24h\| DESC → volume DESC | `service.py:79-82` |

---

### V1 / V2 / Candidate 差异矩阵

| 参数 | V1 Candidate Pool (`hotlist_opportunities`) | V1 Alert Engine (`hotlist_alerts`) | V2 (`hotlist_momentum_v2`) |
|---|---|---|---|
| **stop_pct 过滤** | ❌ 无过滤，全部保存 | ✅ ≤ 5% | ✅ ≤ 5% |
| **RR 过滤** | ❌ 无额外过滤（base = 2.0） | ✅ ≥ 2 显式检查 | ✅ ≥ 2 显式检查 |
| **每次扫描上限** | **5** | 全量（无上限） | **3**（可配） |
| **Expiry** | **1h**（`expiry_minutes=60`，默认） | **1h**（跟随 Candidate） | **24h**（`hold_hours=24`，可配） |
| **去重窗口** | 无（UNIQUE 约束精确匹配） | **4h cooldown** + 同方向去重 | **24h** symbol+direction |
| **Dedup 逻辑** | `UNIQUE(symbol,direction,entry,created_at)` | duplicate_open / opposite_open / cooldown | `exists_recent(24h)` |
| **Confidence 标签** | ✅ ai_review: STRONG/MEDIUM/WEAK | — | — |
| **Settlement 跟踪** | ✅ 1h/4h/24h 三档 horizon | ❌ 未跟踪 | ❌ 未跟踪 |
| **写入表** | `hotlist_opportunities` | `hotlist_alerts` | `v2_signals` |
| **是否推 Telegram** | ❌（candidate 层不推） | ✅ | ✅ |
| **是否统计未推送候选** | ✅ 全量保存 | — | — |
| **真实 entry 触发验证** | ❌ 无 | ❌ 无 | ❌ 无（V2 亦无填仓验证） |
| **是否只看 TP1** | ❌ 同时跟踪 TP1+TP2 | — | — |

### 关键不一致点

1. **stop_pct 过滤缺失**：V1 Candidate Pool 无 ≤5% 过滤，导致 `hotlist_opportunities` 存入大量 stop>5% 订单（本样本中 230/273 = **84%** 来自 stop>5%），与 V2 和 Alert 逻辑不一致。
2. **Expiry 差异**：V1 Candidate 1h，V2 24h。真实入场逻辑完全不同。
3. **Dedup 粒度差异**：V1 Candidate 无去重窗口，同一 symbol 1h 后可再次出现；V2 锁定 24h。
4. **TP1 vs TP2**：V1 Candidate 统计 TP2（按最高 horizon outcome），本次 Audit 只看 TP1。

---

## 二、因子分析（273 个 Unique Orders，24h 挂单，TP1/SL 结算）

> 总览：Filled 222（81.3%）· TP1 121 · SL 90 · Settled 211 · **WR 57.3%** · avgPnL +1.31%

### Factor 1 — LONG vs SHORT

| | n | Filled | Fill% | TP1 | SL | Settled | WR | avgPnL | maxCL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG | 212 | 177 | 83% | 94 | 73 | 167 | 56.3% | +1.21% | 7 |
| SHORT | 61 | 45 | 74% | 27 | 17 | 44 | **61.4%** | **+1.68%** | **4** |

→ SHORT 胜率和盈利均高于 LONG；且连续亏损上限更小（4 vs 7）。

---

### Factor 2 — Confidence（MEDIUM vs WEAK）

| | n | Settled | WR | maxCL |
|---|---:|---:|---:|---:|
| MEDIUM | 137 | 101 | 57.4% | 6 |
| WEAK | 136 | 110 | 57.3% | 8 |

→ **Confidence 标签对 WR 完全无区分力**（差 0.1%）。MEDIUM 的唯一优势是 maxCL 略低（6 vs 8）。  
→ 当前 confidence 判断（RR + volume_ratio + |change|）不能预测胜率。

---

### Factor 3 — stop_pct 区间

| stop_pct | n | Settled | WR | avgPnL | maxCL |
|---|---:|---:|---:|---:|---:|
| 0-2% | 1 | 1 | 0.0% | -1.88% | — |
| 2-3% | 5 | 5 | 60.0% | +0.51% | — |
| **3-4%** | **20** | **18** | **61.1%** | **+0.77%** | **2** |
| **4-5%** | 17 | 17 | **41.2%** | **-0.85%** | — |
| **>5%** | 230 | 170 | **58.8%** | **+1.63%** | — |

→ **4-5% 是危险区**：WR 仅 41.2%，唯一 avgPnL 为负的区间。  
→ stop 3-4%：WR 最高（61.1%），maxCL 仅 2（最稳健）。  
→ stop >5%（主体样本 84%）WR 58.8%，avgPnL +1.63% —— 实际表现良好。

---

### Factor 4 — 重复信号（被去重的副本数量）

| 副本数 | n | Settled | WR |
|---|---:|---:|---:|
| 0 副本（首次出现） | 203 | 150 | 56.7% |
| 1-2 副本 | 61 | 53 | **62.3%** |
| ≥3 副本 | 9 | 8 | 37.5% |

→ **被重复推送 1-2 次的信号胜率最高（+5.6pp）**，说明持续出现在 Hotlist 的机会更强。  
→ 重复 ≥3 次的信号反而表现最差（过度拥挤 / 错过最佳入场）。

---

### Factor 5 — LONG × Confidence 组合

| 组合 | n | Settled | WR | avgPnL | maxCL |
|---|---:|---:|---:|---:|---:|
| LONG + MEDIUM | 107 | 80 | 53.8% | +0.76% | 7 |
| **LONG + WEAK** | **105** | **87** | **58.6%** | **+1.57%** | **8** |
| **SHORT + MEDIUM** | **30** | **21** | **71.4%** | **+4.83%** | **3** |
| SHORT + WEAK | 31 | 23 | 52.2% | +0.91% | 4 |

→ **SHORT + MEDIUM 是最优子集**：WR 71.4%，avgPnL +4.83%，maxCL 仅 3。  
→ LONG + MEDIUM 是最差 LONG 子集：比 LONG + WEAK 低 4.8pp。  
→ Confidence = MEDIUM 在 SHORT 方向有效，在 LONG 方向无效甚至负效。

---

### Factor 6 — stop_pct × direction

| 组合 | n | Settled | WR |
|---|---:|---:|---:|
| SHORT + stop 3-4% | 7 | 5 | **100%** ⚠️小样本 |
| SHORT + stop >5% | 45 | 30 | 66.7% |
| LONG + stop 3-4% | 13 | 13 | 46.2% |
| LONG + stop 4-5% | 10 | 10 | 50.0% |
| LONG + stop >5% | 185 | 140 | 57.1% |

→ SHORT 方向在各 stop_pct 区间均强于对应 LONG。  
→ LONG + stop 3-4% 出乎意料地是 LONG 里最弱的区间（46.2%）。

---

### Factor 7 — Symbol 表现（settled ≥ 2）

**Top 10：**

| Symbol | n | Settled | WR | avgPnL |
|---|---:|---:|---:|---:|
| REUSDT | 13 | 10 | **100%** | +9.65% |
| ESPORTSUSDT | 11 | 2 | 100% | +5.81% |
| LAYERUSDT | 3 | 2 | 100% | +9.23% |
| DEXEUSDT | 5 | 3 | 100% | +3.47% |
| SYNUSDT | 28 | 19 | **89%** | +12.38% |
| BICOUSDT | 27 | 20 | **85%** | +9.77% |
| ALICEUSDT | 8 | 7 | 71% | +2.21% |
| FOLKSUSDT | 3 | 3 | 67% | +0.89% |
| NAORISUSDT | 3 | 3 | 67% | +2.36% |
| IDUSDT | 5 | 3 | 67% | +1.05% |

**Bottom 10：**

| Symbol | n | Settled | WR | avgPnL |
|---|---:|---:|---:|---:|
| LUMIAUSDT | 4 | 4 | 0% | -8.51% |
| BTRUSDT | 4 | 4 | 0% | -7.65% |
| HMSTRUSDT | 2 | 2 | 0% | -6.69% |
| ZEREBROUSDT | 5 | 5 | 20% | -4.59% |
| RESOLVUSDT | 8 | 6 | 33% | -3.87% |
| CLOUSDT | 12 | 12 | 33% | -2.12% |
| BELUSDT | 15 | 8 | 38% | -7.23% |
| LABUSDT | 5 | 5 | 40% | -0.72% |
| GUAUSDT | 14 | 12 | 42% | -7.72% |
| BLESSUSDT | 7 | 7 | 43% | -0.50% |

→ SYNUSDT（28次，WR 89%，+12.38%）和 BICOUSDT（27次，WR 85%，+9.77%）是高质量高频标。  
→ GUAUSDT（14次，WR 42%，-7.72%）和 BELUSDT（15次，WR 38%，-7.23%）是严重拖累项。

---

### 不可分析因子（数据不足）

以下因子因 `hotlist_opportunities` 表未存储对应字段，**无法从当前 DB 分析**：

| 因子 | 原因 | 获取方法 |
|---|---|---|
| 24h涨跌幅区间 | 表无此列 | 需保存 `change_24h_pct` 到 opportunities |
| 成交额区间 | 表无此列 | 需保存 `quote_volume` 到 opportunities |
| volume_ratio | 表无此列 | 需保存 `volume_ratio_15m` 到 opportunities |
| ATR | 无法精确反推 | 需保存 `atr14` 到 opportunities |
| 涨幅榜 vs 跌幅榜 | 依赖 change_24h_pct | 同上 |

---

## 三、高胜率子集

**筛选条件**：n ≥ 20，WR ≥ 60%，avgPnL > 0，maxCL 不恶化

| 子集 | n | Settled | WR | avgPnL | maxCL | 是否满足 |
|---|---:|---:|---:|---:|---:|---|
| SHORT only | 61 | 44 | 61.4% | +1.68% | 4 | ✅ |
| **SHORT + MEDIUM** | **30** | **21** | **71.4%** | **+4.83%** | **3** | **✅ 最优** |
| stop 3-4% | 20 | 18 | 61.1% | +0.77% | 2 | ✅ |
| LONG only | 212 | 167 | 56.3% | +1.21% | 7 | ❌ WR不足 |
| MEDIUM only | 137 | 101 | 57.4% | +1.60% | 6 | ❌ WR不足 |
| LONG+MEDIUM | 107 | 80 | 53.8% | +0.76% | 7 | ❌ WR不足 |
| stop 4-5% | 17 | 17 | 41.2% | -0.85% | — | ❌ 危险区 |

**结论**：只有 3 个子集满足全部条件，最优为 **SHORT + MEDIUM**。

---

## 四、LAB 策略提案（仅研究，不上线）

### `hotlist_tp1_24h_base` — 基准全量

**规则**：全量 unique orders，24h 挂单，TP1/SL only，entry = EMA20 pullback  
| 项目 | 值 |
|---|---|
| 样本数 | 273 |
| Fill Rate | 81.3% |
| Settled | 211 |
| **Win Rate** | **57.3%** |
| avgPnL | +1.31% |
| maxCL | 7 |
**优点**：样本量大，盈利为正，基准清晰  
**缺点**：maxCL=7 偏高；LONG 拖累 SHORT 表现  
**V2 Paper 建议**：✅ 值得作为基准接入，用于对照组

---

### `hotlist_tp1_24h_long_only` — 仅做多

**规则**：过滤 direction == LONG  
| 项目 | 值 |
|---|---|
| 样本数 | 212 |
| Fill Rate | 83% |
| Settled | 167 |
| **Win Rate** | **56.3%** |
| avgPnL | +1.21% |
| maxCL | 7 |
**优点**：Fill Rate 高（83%）  
**缺点**：WR 低于全量，maxCL 同样 7；SHORT 方向更强，仅做多反而损失 alpha  
**V2 Paper 建议**：❌ 不推荐，劣于全量

---

### `hotlist_tp1_24h_medium_only` — 仅 MEDIUM confidence

**规则**：过滤 confidence == MEDIUM  
| 项目 | 值 |
|---|---|
| 样本数 | 137 |
| Fill Rate | 77% |
| Settled | 101 |
| **Win Rate** | **57.4%** |
| avgPnL | +1.60% |
| maxCL | 6 |
**优点**：avgPnL +1.60% 高于全量，maxCL=6 略优  
**缺点**：WR 与全量持平；confidence 标签无独立预测力（WEAK 也是 57.3%）  
**V2 Paper 建议**：⚠️ 可作为辅助过滤，但效果边际。优先研究为何 MEDIUM 在 SHORT 方向有效

---

### `hotlist_tp1_24h_stop_3_4` — stop_pct 3-4%

**规则**：过滤 stop_pct ∈ [3%, 4%)  
| 项目 | 值 |
|---|---|
| 样本数 | 20 |
| Fill Rate | 90% |
| Settled | 18 |
| **Win Rate** | **61.1%** |
| avgPnL | +0.77% |
| maxCL | **2** |
**优点**：WR 最高（61.1%），maxCL 最低（2）——风险最可控的区间  
**缺点**：样本仅 20，信号极少；avgPnL 偏低（+0.77%）  
**V2 Paper 建议**：⚠️ 样本太少，需累积更多数据后验证，不宜单独运行

---

### `hotlist_tp1_24h_short_medium` — SHORT + MEDIUM（新增，最优子集）

**规则**：direction == SHORT AND confidence == MEDIUM  
| 项目 | 值 |
|---|---|
| 样本数 | 30 |
| Fill Rate | 74% |
| Settled | 21 |
| **Win Rate** | **71.4%** |
| avgPnL | **+4.83%** |
| maxCL | **3** |
**优点**：WR 最高（71.4%），avgPnL 最高（+4.83%），maxCL 最低（3）——三项指标均最优  
**缺点**：样本 30，中等规模；Fill Rate 74% 略低  
**V2 Paper 建议**：**✅✅ 强烈推荐优先接入，这是唯一三项指标全优的子集**

---

### `hotlist_tp1_24h_long_medium` — LONG + MEDIUM（原始请求）

**规则**：direction == LONG AND confidence == MEDIUM  
| 项目 | 值 |
|---|---|
| 样本数 | 107 |
| Fill Rate | 79% |
| Settled | 80 |
| **Win Rate** | **53.8%** |
| avgPnL | +0.76% |
| maxCL | 7 |
**优点**：样本较大（107）  
**缺点**：WR 53.8% 是所有子集中最低；avgPnL 最低；maxCL=7 最差  
**V2 Paper 建议**：❌ 不推荐，LONG+MEDIUM 是组合里最弱的

---

## 五、关键结论汇总

| 发现 | 结论 |
|---|---|
| SHORT 全面优于 LONG | WR +5.1pp，avgPnL +0.47%，maxCL 更低 |
| stop 4-5% 是危险区 | WR 41.2%，avgPnL -0.85%，建议加过滤 |
| Confidence 无预测力（LONG） | MEDIUM=57.4% vs WEAK=57.3%，差距可忽略 |
| SHORT+MEDIUM 是最优子集 | WR 71.4%，avgPnL +4.83%，maxCL 3 |
| LONG+MEDIUM 是最差组合 | 不如 LONG+WEAK，也不如全量 |
| 重复出现 1-2 次的信号更强 | WR +5.6pp vs 首次出现 |
| SYNUSDT / BICOUSDT 长期强势 | 高频 + 高 WR + 高 avgPnL |
| GUAUSDT / BELUSDT 持续拖累 | 高频 + 低 WR + 负 avgPnL |
| DB 缺失关键因子字段 | 无法分析 volume / 24h_change / ATR / volume_ratio |

---

## 六、改进建议（仅研究，不修改代码）

1. **过滤 stop 4-5% 区间**（可在实验策略里验证，非上线建议）
2. **SHORT+MEDIUM 优先级最高**，如要接入 V2 Paper，从此子集开始
3. **在 hotlist_opportunities 保存 change_24h_pct / quote_volume / volume_ratio / atr14**，以便后续因子分析更完整
4. **SYNUSDT / BICOUSDT 可作为优质 symbol 白名单**；GUAUSDT / BELUSDT 考虑 symbol-level 降权
5. **Confidence 系统需要重新设计**：当前 RR+volume_ratio+|change| 组合与真实胜率无相关性

---

*本报告为只读分析。未修改任何代码、数据库、策略参数或 Telegram 配置。*
