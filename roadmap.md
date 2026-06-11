# Binance AI Trader V1 — 开发路线图

> 文档状态：Draft（执行计划）
> 更新日期：2026-06-05
> 约束：第一阶段只做数据采集、评分、Top 3、Telegram 和 SQLite；不做实盘下单。

## 1. 当前起点

仓库当前仅包含一个空的 `.gitkeep`，没有代码、依赖、测试、配置或部署资产。因此下面的工作量是从零搭建绿地项目，而不是在已有应用上增量开发。

## 2. 开发策略

采用“先契约、再数据、后评分、最后通知”的纵向推进方式：

1. 先确定领域模型、数据时间语义和验收标准；
2. 建立可替换的 Binance 数据接口和 fixture，避免测试依赖实时网络；
3. 先产出完整候选与评分明细，再做 Top 3；
4. 信号必须先写 SQLite，再尝试 Telegram；
5. 先 shadow mode 观察，不直接对外发布；
6. 每个里程碑都保持可运行、可测试和可回滚。

## 3. 里程碑总览

| 里程碑 | 目标 | 预计工作量* | 主要交付物 |
|---|---|---:|---|
| M0 | 工程基线与领域契约 | 1–2 天 | 项目骨架、配置、CI、模型、测试基线 |
| M1 | Binance 公共数据层 | 3–5 天 | universe、行情采集、限流、数据校验 |
| M2 | 大盘与板块分析 | 3–4 天 | BTC/ETH regime、板块映射与排名 |
| M3 | 特征、评分与风险位 | 4–6 天 | 评分引擎、entry/SL/TP/RR、解释 |
| M4 | Top 3 与 SQLite | 3–4 天 | 排序去重、schema、幂等、运行审计 |
| M5 | Telegram 与调度 | 2–3 天 | 消息、重试、单实例调度、告警 |
| M6 | 回放、shadow 与上线准备 | 2–4 周观察 | 回放报告、参数冻结、运维文档 |

\* 估算以 1 名熟悉 Python/量化数据工程的开发者为参考，不含外部合规审查；应在 M0 后根据数据接口验证结果重估。

## 4. 详细计划

## M0 — 工程基线与领域契约

### 目标

建立一个可安装、可测试、可静态检查的 Python 项目，并冻结第一版数据契约。

### 工作项

- [ ] 创建 `pyproject.toml` 和锁文件，固定 Python 与依赖版本；
- [ ] 建立 `src/` layout、测试目录和模块边界；
- [ ] 配置 Ruff、类型检查、pytest 和 coverage；
- [ ] 建立 CI：lint、type check、unit tests；
- [ ] 定义 `Direction`、`MarketRegime`、`Candle`、`Ticker`、`Contract`、`ScoreBreakdown`、`Signal` 等领域模型；
- [ ] 定义对外信号 JSON schema，确保包含：`symbol`、`direction`、`entry`、`stop_loss`、`stop_loss_pct`、`TP1`、`TP2`、`RR`、`logic_summary`；
- [ ] 定义 UTC、已收盘 K 线、价格 Decimal/舍入等统一约定；
- [ ] 建立配置分层、`.env.example` 与秘密管理规则；
- [ ] 添加日志规范、错误分类和 `run_id` 贯穿规则；
- [ ] 编写 README 的本地启动和测试占位流程。

### 验收标准

- [ ] 全新环境可通过一个命令安装依赖；
- [ ] lint、类型检查和空项目测试在 CI 通过；
- [ ] Signal schema 能拒绝非法方向、负价格和错误的 TP/SL 排列；
- [ ] 项目中不存在 Binance 私有账户或订单接口；
- [ ] 配置缺失或非法时 fail fast，且日志不泄露秘密。

## M1 — Binance 公共数据层

### 目标

稳定发现全部合格的 USDT 永续合约，并为后续分析提供时间一致、经过校验的数据快照。

### 工作项

- [ ] 实现只读 Binance 公共 REST client abstraction；
- [ ] 获取合约信息，筛选 USDT 本位、永续、可交易标的；
- [ ] 保存 tick size、价格精度和合约状态；
- [ ] 获取 24h ticker 并完成流动性初筛；
- [ ] 获取 BTC/ETH 及候选标的 15m、1h、4h 已收盘 K 线；
- [ ] 可选采集 funding rate 和 open interest，并定义不可用时的降级规则；
- [ ] 实现连接池、超时、有限重试、指数退避、抖动和有界并发；
- [ ] 建立限流预算与请求统计，避免高并发扫全市场导致封禁；
- [ ] 校验 K 线连续性、重复、顺序、OHLC 合法性、数量和新鲜度；
- [ ] 建立数据 fixture 与契约测试；
- [ ] 对单 symbol 错误做隔离，并输出完整失败原因。

### 验收标准

- [ ] 一次命令能打印 universe 总数与有效数据标的数；
- [ ] 所有指标输入只含已收盘 K 线；
- [ ] 单标的接口失败不会终止整批扫描；
- [ ] BTC/ETH 关键数据失败会关闭信号发布；
- [ ] 测试使用 fixture 时无需访问互联网；
- [ ] 请求速率、重试和失败均可从日志审计。

## M2 — BTC/ETH 市场状态与板块强弱

### 目标

给每批扫描建立统一的风险背景，并为每个标的提供可解释的板块相对强度。

### 工作项

- [ ] 实现 EMA、ATR%、ADX/趋势强度、收益率和相对成交量基础指标；
- [ ] 为 BTC、ETH 分别计算 1h/4h 状态；
- [ ] 实现 `BULL_TREND`、`BEAR_TREND`、`RANGE`、`HIGH_VOLATILITY`、`DATA_INVALID` 状态机；
- [ ] 实现 BTC/ETH 综合 regime 和 LONG/SHORT 方向门控；
- [ ] 创建版本化 `config/sectors.yaml`；
- [ ] 定义主板块、标签、`OTHER` 和新增 symbol 未映射告警；
- [ ] 实现板块收益、趋势广度、成交额扩张和鲁棒聚合；
- [ ] 限制最小板块成分数及单一大市值成分权重；
- [ ] 输出 sector score/rank/direction/breadth；
- [ ] 为不同市场状态与小样本板块建立测试 fixture。

### 验收标准

- [ ] 同一数据快照产生完全一致的 market regime；
- [ ] regime 对 LONG/SHORT 的允许、提高门槛或禁止行为有测试覆盖；
- [ ] 每个可评分标的均有主板块或明确为 `OTHER`；
- [ ] 新上市未映射标的不会静默获得板块加分；
- [ ] 小板块和缺失数据不会扭曲全市场排名。

## M3 — 特征、评分与信号风险位

### 目标

建立可解释的 100 分评分体系，并为合格候选生成数值合法的 entry、stop、TP1、TP2 和 RR。

### 工作项

- [ ] 实现硬过滤：流动性、数据完整性、ATR%、极端波动、funding、方向门控和历史长度；
- [ ] 实现 4h 高周期趋势分；
- [ ] 实现 1h 趋势/动量分；
- [ ] 实现 15m 入场质量分；
- [ ] 实现大盘一致性和板块强度分；
- [ ] 实现量能/OI 共振分与拥挤惩罚；
- [ ] 实现分项归一化、权重和总分；
- [ ] 分别实现 LONG/SHORT 镜像逻辑，避免符号方向错误；
- [ ] 使用 swing structure + ATR buffer 构造 stop loss；
- [ ] 计算 TP1、TP2、stop_loss_pct 和 RR；
- [ ] 根据 tick size 对输出价格做保守舍入；
- [ ] 淘汰止损过近/过远、RR 不足或价格过度延伸的候选；
- [ ] 保存每项原始值、规则 ID、分数贡献和淘汰原因；
- [ ] 用确定性模板生成 `logic_summary`；
- [ ] 创建黄金样本测试，锁定预期评分结果。

### 初始评分基线

| 分项 | 初始权重 |
|---|---:|
| BTC/ETH 大盘一致性 | 15 |
| 板块强度 | 15 |
| 4h 趋势 | 20 |
| 1h 趋势/动量 | 20 |
| 量能与参与度 | 10 |
| 15m 入场质量 | 10 |
| 风险收益质量 | 10 |
| 拥挤/异常惩罚 | 0 至 -20 |

初始最低总分建议为 70，最低 RR 建议围绕 TP2 的 2.0 设计；这些不是最终生产参数，必须在 M6 校准并版本化。

### 验收标准

- [ ] 每个分数都能追溯到输入值和规则；
- [ ] 相同输入、配置和算法版本输出一致；
- [ ] LONG 满足 `SL < entry < TP1 < TP2`；
- [ ] SHORT 满足 `SL > entry > TP1 > TP2`；
- [ ] `stop_loss_pct` 和 RR 可由输出字段独立复算；
- [ ] 价格符合标的 tick size；
- [ ] 无法形成有效风险结构的候选不会被强行输出。

## M4 — Top 3、SQLite 与幂等

### 目标

从合格候选确定性选出最多 3 个高质量、不过度集中的信号，并保存完整历史。

### 工作项

- [ ] 定义稳定排序：总分、RR、流动性、symbol；
- [ ] 实现同 symbol 去重、板块上限和相关性/同质化限制；
- [ ] 实现冷却期，防止连续周期重复轰炸同一信号；
- [ ] 明确“最多 3 个，不强制凑满”的产品行为；
- [ ] 建立 migration 机制；
- [ ] 创建 `scan_runs`、`signals`、`candidate_scores`、`notification_attempts`；
- [ ] 开启 foreign keys、WAL、busy timeout 和事务边界；
- [ ] 实现 repository abstraction；
- [ ] 信号与评分在同一事务中写入；
- [ ] 建立批次键和信号唯一约束；
- [ ] 实现数据库保留/归档策略；
- [ ] 提供按批次、symbol、时间范围查询历史的 CLI。

### 验收标准

- [ ] 输出只能是 0–3 个信号；
- [ ] tie-break 结果稳定且有测试；
- [ ] 同一 candle close 重跑不会插入重复信号；
- [ ] 数据库写入失败时绝不发送通知；
- [ ] 每个已发布信号可追溯到批次、特征、评分和配置版本；
- [ ] 进程崩溃后可判断批次状态并安全恢复。

## M5 — Telegram、调度与运行保障

### 目标

以幂等、可重试且不泄露秘密的方式定时发布信号。

### 工作项

- [ ] 实现 Telegram client abstraction 和 `sendMessage`；
- [ ] 创建 Top 3 摘要模板和无信号模板；
- [ ] 安全转义 symbol、摘要和格式化内容；
- [ ] 添加 UTC 时间、market regime、run ID 和风险声明；
- [ ] 将 token/chat ID 从环境变量注入；
- [ ] 分类处理 timeout、429、5xx、4xx 等错误；
- [ ] 实现有限重试和通知 attempt 记录；
- [ ] 成功后保存 Telegram message ID；
- [ ] 避免进程重启导致重复推送；
- [ ] 提供 `scan-once` 命令，便于 cron 和人工运行；
- [ ] 选择 cron 或单进程调度器，并加进程锁防止重叠扫描；
- [ ] 添加连续失败、universe 异常和数据陈旧告警；
- [ ] 提供 Dockerfile、健康检查和部署说明。

### 验收标准

- [ ] Telegram 消息完整包含产品要求的 9 个字段；
- [ ] bot token 不出现在 Git、日志、SQLite 或异常追踪中；
- [ ] Telegram 暂时失败不会丢失已生成信号；
- [ ] 重试成功后不会再次发送；
- [ ] 两个调度周期不能并发执行；
- [ ] 单次扫描结束码能区分成功、部分成功和失败。

## M6 — 历史回放、Shadow Mode 与上线准备

### 目标

验证系统没有时间穿越、数据与运行缺陷，并以观察数据校准首版阈值。

### 工作项

- [ ] 构建固定历史数据回放入口，复用生产评分代码；
- [ ] 覆盖牛市、熊市、震荡、高波动和极端行情样本；
- [ ] 校验所有特征只读取信号时点之前的已收盘数据；
- [ ] 统计信号频率、方向、板块集中度、分数分布和淘汰原因；
- [ ] 增加 outcome 评估：MFE、MAE、TP1/TP2/SL 先后触达；
- [ ] 对关键阈值做敏感性分析，防止参数过拟合；
- [ ] 运行 2–4 周 shadow mode：完整落库，不向公开频道推送；
- [ ] 记录 API 稳定性、批次耗时、缺失数据率和通知测试结果；
- [ ] 冻结 `algorithm_version` 与 `config_version`；
- [ ] 完成 runbook、备份/恢复、token 轮换和故障演练；
- [ ] 经人工评审后启用正式 Telegram 目标。

### 上线门槛

- [ ] 核心计算和编排测试通过；
- [ ] 历史回放未发现未来数据泄漏；
- [ ] shadow 期间批次成功率达到团队设定 SLO；
- [ ] 无重复推送、错向 TP/SL 或非法价格；
- [ ] 请求量长期处于安全预算内；
- [ ] 评分分布不过度饱和，且不是每轮强制产生信号；
- [ ] 运维人员能依据 runbook 恢复数据库和轮换 token；
- [ ] 风险声明和适用地区合规检查完成。

## 5. 建议 Sprint 划分

### Sprint 1：可获取且可信的数据

范围：M0 + M1。

演示结果：运行 `scan-once --dry-run`，输出 universe、BTC/ETH 和随机抽样标的的数据质量报告，不产生信号。

### Sprint 2：可解释候选

范围：M2 + M3。

演示结果：对固定 fixture 输出所有候选、分项评分、淘汰原因和有效风险位，不做 Telegram 推送。

### Sprint 3：完整 V1 闭环

范围：M4 + M5。

演示结果：定时扫描 → Top 3 → SQLite → 测试 Telegram 频道；故障注入后可恢复且不重复推送。

### Sprint 4：验证与发布

范围：M6。

演示结果：回放报告、shadow 运行报告、冻结参数和正式部署 runbook。

## 6. 测试矩阵

| 层级 | 必测内容 | 是否允许实时网络 |
|---|---|---|
| Unit | 指标、regime、板块、评分、风险位、排序、格式化 | 否 |
| Contract | Binance/Telegram 响应 mapper | 否，使用 fixture |
| Integration | 编排、SQLite、migration、幂等、失败恢复 | 否，使用 fake server/client |
| Smoke | 公共 Binance 数据连通性、Telegram 测试目标 | 是，显式运行 |
| Replay | 历史数据、时间一致性、结果统计 | 使用冻结数据集 |
| Shadow | 真实定时运行但不公开发信号 | 是 |

CI 中不得把外部 API 可用性作为常规测试通过的前提。

## 7. 首版任务优先级

### P0 — 阻塞 V1

- 仓库工程基线；
- 公共市场数据和限流；
- 数据质量闸门；
- BTC/ETH regime；
- 板块映射和评分；
- symbol 评分、风险位和 Top 3；
- SQLite、幂等和 Telegram；
- 测试、日志、shadow mode；
- 明确无交易能力。

### P1 — V1 稳定性增强

- open interest 降级特征；
- signal outcome 跟踪；
- 健康指标导出；
- 数据库自动备份和保留策略；
- 管理未映射 symbol 的报告；
- 通知模板本地化。

### P2 — 后续版本候选

- Web 仪表盘；
- PostgreSQL；
- WebSocket 增量数据；
- 多策略与策略对比；
- 机器学习 ranking（仅在有足够无泄漏数据后）；
- 用户订阅与个性化过滤；
- 回测可视化。

实盘交易、API Key、账户资产和自动下单不在上述 V1/P1/P2 默认范围内；如未来立项，必须独立安全评审、风险系统设计和产品批准。

## 8. 风险登记表

| 风险 | 影响 | 缓解措施 | 验证点 |
|---|---|---|---|
| 扫全市场触发限流 | 数据不全/封禁 | 并发上限、预算、缓存、退避 | M1 压力与错误注入 |
| 使用未收盘 K 线 | 信号漂移、回测失真 | closed-candle 过滤、时间契约 | M1/M6 测试 |
| 幸存者偏差 | 评估虚高 | 保存历史 universe；回放注明限制 | M6 报告 |
| 板块映射过时 | 板块分误导 | 版本化映射、未映射告警 | M2 每轮报告 |
| 低流动性标的高分 | 无法合理成交 | 24h 成交额、spread、滑点代理过滤 | M1/M3 |
| 极端 funding/OI 拥挤 | 反转/挤仓风险 | 硬阈值和惩罚项 | M3 fixture |
| SQLite 锁/损坏 | 历史或幂等失效 | 单写者、WAL、事务、备份 | M4/M6 演练 |
| Telegram 重复消息 | 用户体验差 | outbox/attempt、唯一键、恢复逻辑 | M5 故障注入 |
| 参数过拟合 | 实盘观察失效 | 少参数、跨阶段回放、shadow、版本冻结 | M6 |
| “AI”造成不当预期 | 合规与信任风险 | 明确规则系统、解释和风险声明 | 文案评审 |
| API/字段变化 | 采集失败 | mapper、契约 fixture、异常告警 | M1/M5 |
| 无合格信号仍凑 Top 3 | 质量下降 | 最低分与 0–3 产品约定 | M4 测试 |

## 9. 项目管理与 Definition of Done

每个开发任务完成时必须同时满足：

- [ ] 代码遵循模块依赖方向；
- [ ] 有成功路径和失败路径测试；
- [ ] 不依赖实时网络的测试可重复运行；
- [ ] 配置和行为有文档；
- [ ] 日志包含必要上下文且无秘密；
- [ ] migration 可向前执行；
- [ ] 错误不会被静默吞掉；
- [ ] 不引入任何下单能力；
- [ ] 通过 lint、类型检查和测试；
- [ ] 对可见行为的变更更新 changelog/README。

## 10. 第一批可执行 Issue 清单

建议按以下顺序创建 issue：

1. `chore: bootstrap Python project and CI quality gates`
2. `feat: define domain models and public signal schema`
3. `feat: add validated settings and secret handling`
4. `feat: implement read-only Binance public client`
5. `feat: build USDT perpetual universe and metadata cache`
6. `feat: collect and validate closed 15m/1h/4h candles`
7. `feat: implement BTC and ETH market regime engine`
8. `feat: add versioned sector mapping and strength ranking`
9. `feat: implement symbol feature engine and hard filters`
10. `feat: implement deterministic scoring and explanations`
11. `feat: build entry stop TP and RR calculator`
12. `feat: add deterministic diversified Top 3 selector`
13. `feat: add SQLite migrations and repositories`
14. `feat: persist scan runs candidates signals and attempts`
15. `feat: add Telegram formatter client and idempotent delivery`
16. `feat: add scan-once command scheduler lock and Docker image`
17. `test: add end-to-end fake-provider scan scenarios`
18. `feat: add replay and signal outcome evaluation`
19. `ops: run shadow mode and publish readiness report`
20. `docs: finalize operating runbook and release checklist`

## 11. 产品验收示例

一轮成功扫描应具备以下可观测结果：

1. 创建一个 `scan_run`，记录开始时间和配置版本；
2. 刷新或读取 USDT 永续 universe；
3. 获取并验证 BTC/ETH 与候选行情；
4. 计算 market regime 和板块排名；
5. 为每个有效标的计算 LONG/SHORT 候选及淘汰原因；
6. 对合格候选计算 entry、SL、TP1、TP2、RR；
7. 按稳定规则和多样性约束选出 0–3 个；
8. 在单个事务中保存候选、信号和批次统计；
9. 向 Telegram 发送一条摘要，或记录无信号结果；
10. 保存通知结果并结束批次；
11. 任何环节都没有访问账户或提交订单。

## 12. 开工前需由产品负责人确认的参数

这些问题不阻塞总体架构，但应在 M2/M3 实现前确认并写入版本化配置：

- 扫描周期是否固定为 15 分钟；
- 最低 24h USDT 成交额；
- 是否排除新上市不足 N 天的合约；
- 允许的最小/最大 stop loss 百分比；
- 最低总分和最低 RR；
- 高波动 regime 是完全静默还是提高门槛；
- 同板块最多允许几个信号；
- 相同 symbol 的冷却周期；
- 无合格信号时 Telegram 静默还是发送摘要；
- Telegram 展示时区和语言；
- shadow mode 的最短时长与上线 SLO；
- 板块分类的业务负责人和更新流程。

在这些参数确认前，可以使用明确标记为 `baseline-not-production` 的默认值开发和测试，但不得将其宣传为已验证策略。
