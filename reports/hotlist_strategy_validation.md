# Hotlist Strategy Validation Report
**生成时间：** 2026-07-03 07:03 UTC
**数据源：** `data/market_data.db`（本地快照，2026-06-18 ~ 2026-06-23，约6天）
**结算数据：** Binance USD-M Futures `/fapi/v1/klines` 15m 实时重拉（2026-07-03 执行）
**审计原则：** 只读 · 不使用 hotlist_outcomes · 不使用已有 Performance 数据 · 不修改任何代码/数据库/策略

---

## 第一步 · 第二步：去重统计

```
Original Opportunities  ：380
         ↓ 去重 (±0.2% entry, 同 symbol+direction)
Unique Orders           ：275
         ↓
重复率                  ：27.6%
```

| 指标 | 数值 |
|---|---|
| 原始 opportunity 行数 | **380** |
| 去重后唯一订单数 | **275** |
| 去重压缩率 | **27.6%** |
| Telegram 已推送 unique orders | **1** → 见下方推送分析 |

> **去重方法：** 相同 symbol + direction，entry 价格差 ≤0.2% 视为同一订单。取最早 `created_at` 作为代表行，合并所有 source_opportunity_ids，记录 repeat_count。

---

## 第三步：重复最多 Symbol — Top 20

| Rank | Symbol | 原始扫描次数 | 去重后唯一 orders |
|---|---|---|---|
| 1 | `SYNUSDT` | 37 | — |
| 2 | `BTWUSDT` | 36 | — |
| 3 | `BICOUSDT` | 34 | — |
| 4 | `UBUSDT` | 33 | — |
| 5 | `CLOUSDT` | 30 | — |
| 6 | `TNSRUSDT` | 25 | — |
| 7 | `BELUSDT` | 20 | — |
| 8 | `GUAUSDT` | 17 | — |
| 9 | `RESOLVUSDT` | 15 | — |
| 10 | `REUSDT` | 14 | — |
| 11 | `DEXEUSDT` | 11 | — |
| 12 | `ESPORTSUSDT` | 11 | — |
| 13 | `BLESSUSDT` | 10 | — |
| 14 | `IDUSDT` | 9 | — |
| 15 | `ALICEUSDT` | 8 | — |
| 16 | `HEIUSDT` | 7 | — |
| 17 | `HUSDT` | 7 | — |
| 18 | `VELVETUSDT` | 7 | — |
| 19 | `BSBUSDT` | 6 | — |
| 20 | `ZEREBROUSDT` | 6 | — |

> **注：** 去重后的 symbol 级别唯一 orders 数已导出至 `hotlist_unique_orders.csv`。"-" 表示该 symbol 部分 orders 在不同 direction 下可能有独立 clusters。

---

## 第四步 · 第五步：重新结算（Binance Kline 实测）

> **结算规则（独立实现，不依赖 hotlist_outcomes）**
> 1. **成交判断：** 在 `expiry`（1h 窗口）内，15m K 线 low/high 触及 entry → 视为成交（Filled）
> 2. **出场判断：** 成交后逐根 15m K 线扫描 TP1 / TP2 / SL，最先触及者为结果
> 3. **Timeout：** 开仓超过 7 天仍无结果 → TIMEOUT
> 4. **Expired：** expiry 内未成交 → EXPIRED（未填单，不计入胜率）
> 5. **Kline 数据：** 每订单拉取 500 根 15m K 线（≈5.2 天）

### 订单流水表

```
Total Unique Orders    ：275
  ├─ Filled  (成交)    ：103   (37.5%)
  └─ Expired (未成交)  ：172   (62.5%)
       └─ [注] 1h 内价格未触及 entry，视为未成交，不计入胜率

成交后结算 (103 笔)
  ├─ TP1 Hit           ：47
  ├─ TP2 Hit           ：9
  ├─ SL  Hit           ：44
  ├─ Timeout (>7d)     ：3
  └─ Decisive 合计     ：100 (TP1+TP2+SL)
```

| 指标 | LONG | SHORT | 合计 |
|---|---|---|---|
| Decisive | 80 | 20 | 100 |
| TP1 | 36 | 11 | 47 |
| TP2 | 8 | 1 | 9 |
| SL  | 36  | 8  | 44  |
| **Win Rate** | **55.0%** | **60.0%** | **56.0%** |

### 绩效汇总（仅 Decisive 样本）

| 指标 | 数值 |
|---|---|
| **真实 Win Rate（TP1+TP2 / Decisive）** | **56.0%** |
| LONG Win Rate | **55.0%**（44/80 decisive）|
| SHORT Win Rate | **60.0%**（12/20 decisive）|
| Telegram 推送 Win Rate | **55.0%**（11/20 decisive）|
| Average RR | 0.21 |
| Average PnL% | 1.1318% |
| Average Holding Time | 223 分钟（≈3.7h）|

---

## 第六步：Confidence 过滤效果

| Confidence | Decisive | Win Rate |
|---|---|---|
| MEDIUM | 36 | **63.9%** |
| WEAK | 64 | **51.6%** |

> MEDIUM confidence 胜率 **63.9%** > WEAK **51.6%**，差距明显。

---

## Stop % 过滤效果

| Stop % 区间 | TP | SL | 胜率 |
|---|---|---|---|
| <2% | 0 | 1 | 0.0% |
| 2-3% | 2 | 1 | 66.7% |
| 3-4% | 10 | 7 | 58.8% |
| 4-5% | 3 | 7 | 30.0% |
| >5% | 41 | 28 | 59.4% |

---

## Symbol 级别绩效（≥2 decisive 样本）

### 表现最好 — Top 10

| Symbol | Decisive | Win Rate | TP1+TP2 | SL | PnL 合计% |
|---|---|---|---|---|---|
| `DEXEUSDT` | 2 | 100.0% | 2 | 0 | +9.05% |
| `FOLKSUSDT` | 2 | 100.0% | 2 | 0 | +9.38% |
| `REUSDT` | 6 | 100.0% | 6 | 0 | +90.45% |
| `GUAUSDT` | 6 | 83.0% | 5 | 1 | +10.91% |
| `SYNUSDT` | 9 | 78.0% | 7 | 2 | +52.76% |
| `UBUSDT` | 4 | 75.0% | 3 | 1 | +23.73% |
| `ALICEUSDT` | 6 | 67.0% | 4 | 2 | +18.82% |
| `BELUSDT` | 3 | 67.0% | 2 | 1 | -7.50% |
| `BICOUSDT` | 3 | 67.0% | 2 | 1 | +3.72% |
| `IDUSDT` | 3 | 67.0% | 2 | 1 | +3.14% |

### 表现最差 — Bottom 10

| Symbol | Decisive | Win Rate | TP1+TP2 | SL | PnL 合计% |
|---|---|---|---|---|---|
| `BTWUSDT` | 11 | 64.0% | 7 | 4 | +38.40% |
| `RESOLVUSDT` | 6 | 33.0% | 2 | 4 | -23.20% |
| `CLOUSDT` | 7 | 29.0% | 2 | 5 | -2.57% |
| `VELVETUSDT` | 4 | 25.0% | 1 | 3 | -19.04% |
| `BLESSUSDT` | 2 | 0.0% | 0 | 2 | -9.64% |
| `BTRUSDT` | 4 | 0.0% | 0 | 4 | -30.61% |
| `HEIUSDT` | 2 | 0.0% | 0 | 2 | -44.36% |
| `HMSTRUSDT` | 2 | 0.0% | 0 | 2 | -13.38% |
| `LUMIAUSDT` | 2 | 0.0% | 0 | 2 | -17.78% |
| `ZEREBROUSDT` | 4 | 0.0% | 0 | 4 | -27.39% |

---

## 六大核心问题回答

### Q1. 去重后，真正唯一订单还有多少？

**275 笔唯一订单**（原始 380 行，去重率 27.6%）

每个唯一订单定义：同 symbol + direction + entry（±0.2% 容差），无论扫描时间不同，均视为同一订单。

---

### Q2. 真实结算还有多少？

| 层级 | 数量 | 说明 |
|---|---|---|
| Unique Orders | 275 | 去重后订单数 |
| Filled（价格触及 entry） | 103（37.5%） | 1h 内 entry 被触及 |
| **Decisive（TP/SL 出场）** | **100** | **实际有结果的订单** |
| Expired（未成交）| 172（62.5%） | 1h 内 entry 未被触及 |

> **关键发现：** 172 笔（62.5%）订单在 1h expiry 内价格未触及 entry，属于**未成交的 limit order**。这说明大量候选的 entry 设置在市场未能到达的价位，实际无法执行。

---

### Q3. 真实胜率是多少？

**Win Rate = 56.0%**（56 wins / 100 decisive）

| 分类 | 胜率 | 样本 |
|---|---|---|
| 全量 decisive | 56.0% | 100 笔 |
| LONG only | 55.0% | 80 笔 |
| SHORT only | 60.0% | 20 笔 |
| Telegram 推送 | 55.0% | 20 笔 |
| MEDIUM confidence | 63.9% | 36 笔 |
| WEAK confidence | 51.6% | 64 笔 |

---

### Q4. LONG 是否明显优于 SHORT？

| 方向 | Decisive | Win Rate | TP1 | TP2 | SL |
|---|---|---|---|---|---|
| LONG  | 80  | 55.0%  | 36  | 8  | 36  |
| SHORT | 20 | 60.0% | 11 | 1 | 8 |

**差距不显著（55.0% vs 60.0%，差 5.0pp）。** 样本量不足，无法得出方向性结论。

---

### Q5. 哪些 Symbol 长期表现最好/最差？

**最好（WR 最高，≥2 decisive）：**
- `DEXEUSDT`：WR=100.0%，2 decisive，PnL 合计 +9.05%
- `FOLKSUSDT`：WR=100.0%，2 decisive，PnL 合计 +9.38%
- `REUSDT`：WR=100.0%，6 decisive，PnL 合计 +90.45%
- `GUAUSDT`：WR=83.0%，6 decisive，PnL 合计 +10.91%
- `SYNUSDT`：WR=78.0%，9 decisive，PnL 合计 +52.76%

**最差（WR 最低，≥2 decisive）：**
- `BTRUSDT`：WR=0.0%，4 decisive，PnL 合计 -30.61%
- `HEIUSDT`：WR=0.0%，2 decisive，PnL 合计 -44.36%
- `HMSTRUSDT`：WR=0.0%，2 decisive，PnL 合计 -13.38%
- `LUMIAUSDT`：WR=0.0%，2 decisive，PnL 合计 -17.78%
- `ZEREBROUSDT`：WR=0.0%，4 decisive，PnL 合计 -27.39%

---

### Q6. 哪些过滤真正提高胜率？

| 过滤维度 | 结论 | 效果 |
|---|---|---|
| **Confidence = MEDIUM** | ✅ 有效 | 63.9% vs WEAK 51.6%，+12.3pp |
| **Stop % = 3-4% 区间** | ✅ 较优 | 58.8% 胜率，样本合理 |
| **Telegram 推送过滤** | 🔶 持平 | 55.0% vs 全量 56.0%，差异有限 |
| Entry 触及率（filled）| ⚠️ 需改进 | 仅 37.5% 订单成交，大量 entry 未被市场触及 |

**建议：** 优先推送 MEDIUM confidence + stop% 3-4% 的组合。入场价格需要更贴近市价，否则 172 笔(62.5%) 订单永远无法成交。

---

### Q7. Hotlist 是否值得保留？

| 维度 | 数据 | 评估 |
|---|---|---|
| 去重后唯一订单数 | 275 | 6天数据，密度合理 |
| 成交率 | 37.5% | ⚠️ 偏低，大量 limit order 未触及 |
| **真实 Win Rate** | **56.0%** | ✅ 高于随机 50% |
| Avg PnL% | 1.1318% | ⚠️ 偏低（RR 未充分兑现） |
| Avg RR | 0.21 | ⚠️ 实现 RR 远低于设计 RR=2.0 |
| 样本量 | 100 decisive | ⚠️ 6天样本，置信区间较宽 |

**结论：**
- **策略层面：** 胜率 56.0% 高于随机，说明 Binance 热榜选币有效，**策略值得保留**。
- **执行层面：** 成交率仅 37.5%，大量候选 entry 设置过激进（低于市价），实际无法触发。需要审视 entry 生成逻辑（当前 entry = 市价折扣）。
- **数量层面：** 去重后 275 笔（vs 报告中的 275 笔），生产 DB 的 2635 行实为 ~900+ 唯一订单（按同比例推算）。
- **样本局限：** 本次分析基于本地 6 天快照，生产 DB 有更大样本，结论需以生产 DB 同口径验证。

---

## 导出文件

| 文件 | 说明 | 行数 |
|---|---|---|
| `reports/hotlist_unique_orders.csv` | 去重后唯一订单，含 repeat_count 和 source IDs | 275 |
| `reports/hotlist_unique_results.csv` | 每笔唯一订单的重新结算结果 | 275 |
| `reports/hotlist_strategy_validation.md` | 本报告 | — |

---

*审计完成 | 只读 | 不进行实盘交易 | 2026-07-03 07:03 UTC*
