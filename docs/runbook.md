# Binance AI Trader V1 运行手册

本文面向从 GitHub 导入项目后，只使用手机、Replit Shell 或普通 Linux/macOS 终端的用户。系统仅调用 Binance USDⓈ-M Futures 公开行情接口，不需要 API Key，不访问账户，不下单，只生成 LONG 分析信号。

## 1. 运行前检查

需要：

- Python 3.11 或更高版本；
- 项目根目录中的 `src/`、`config/`、`tests/` 和 `data/`；
- 执行真实 `scan` 时，可以访问 `https://fapi.binance.com`；
- 至少保留 `data/.gitkeep`，程序会自动创建 SQLite 数据库目录和表。

进入项目根目录后，先执行：

```bash
python --version
pwd
```

如果 `python --version` 低于 3.11，请切换到 Python 3.11+ 环境。所有命令都应从包含 `pyproject.toml` 的仓库根目录执行。

## 2. 一键冒烟检查

```bash
bash scripts/smoke_test.sh
```

脚本依次检查 Python 编译、全部离线测试、根 CLI 帮助、`scan` 帮助和 `backtest` 帮助。测试使用本地 fixture/fake，不访问 Binance，也不会写入生产数据库。

如果手机不方便输入长命令，直接复制上面的整行，粘贴到 Shell 后按 Enter。

## 3. 数据库位置和备份

默认数据库文件：

```text
data/market_data.db
```

SQLite 使用 WAL，因此运行过程中还可能看到：

```text
data/market_data.db-wal
data/market_data.db-shm
```

下载或复制数据库前：

1. 等待当前命令结束；
2. 不要让另一个 `scan`、`evaluate` 或 `backtest` 同时写数据库；
3. 确认 WAL 已回写：

```bash
python - <<'PY'
import sqlite3
connection = sqlite3.connect("data/market_data.db")
connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
connection.close()
print("database checkpoint complete")
PY
```

然后下载 `data/market_data.db`。不要只下载 `-wal` 或 `-shm` 文件。

可选备份命令：

```bash
cp data/market_data.db "data/market_data-backup-$(date +%Y%m%d-%H%M%S).db"
```

## 4. 运行真实 scan

```bash
PYTHONPATH=src python -m binance_ai_trader scan \
  --database data/market_data.db \
  --config config/universe.json \
  --sectors-config config/sectors.json \
  --kline-limit 200 \
  --max-workers 5
```

流程顺序：公开行情采集 → BTC/ETH Regime → Scores → Sector Strength → Regime/Sector Gate → 最多三个 LONG signals。

输出是 JSON Lines，每行一个信号。没有输出不一定是错误：`BEAR`/`OBSERVE` 会阻止 LONG，或者没有候选满足 Entry、SL、TP 和 RR 条件。

退出码：

- `0`：本轮请求全部成功；
- `2`：至少一个 symbol/interval 请求失败，完整数据的 symbol 仍可能继续处理；
- 其他非零值：命令执行错误，请查看最后一段 traceback。

保守运行方式（减少并发和代理压力）：

```bash
PYTHONPATH=src python -m binance_ai_trader scan \
  --database data/market_data.db \
  --config config/universe.json \
  --sectors-config config/sectors.json \
  --kline-limit 200 \
  --max-workers 1 \
  --timeout 20 \
  --max-retries 5
```

## 5. 查看 BTC/ETH 市场状态

先至少成功执行一次 `scan`，再运行：

```bash
PYTHONPATH=src python -m binance_ai_trader regime \
  --database data/market_data.db
```

输出包含 `btc_regime`、`eth_regime` 和 `combined_regime`。该命令读取 SQLite 中已有 K 线，不主动下载行情。

## 6. 查看板块排名

```bash
PYTHONPATH=src python -m binance_ai_trader sectors \
  --database data/market_data.db \
  --config config/sectors.json
```

该命令读取最近一次 scores 和对应 universe snapshot，输出板块 JSON Lines，并保存到 `sector_snapshots`。

## 7. 评估已生成信号

```bash
PYTHONPATH=src python -m binance_ai_trader evaluate \
  --database data/market_data.db
```

`evaluate` 只读取数据库。信号生成后需要继续积累未来已收盘 15m K 线；如果不足完整窗口且尚无终局结果，信号保持 pending，不会提前计为 `EXPIRED`。

推荐顺序：

1. 定期执行 `scan`；
2. 等待未来 K 线被后续扫描写入数据库；
3. 执行 `evaluate`；
4. 保存输出并备份数据库。

## 8. 运行 Historical Backtest

```bash
PYTHONPATH=src python -m binance_ai_trader backtest \
  --database data/market_data.db \
  --config config/sectors.json \
  --step-bars 1
```

可选时间范围使用 Unix 毫秒：

```bash
PYTHONPATH=src python -m binance_ai_trader backtest \
  --database data/market_data.db \
  --config config/sectors.json \
  --start-ms 1767225600000 \
  --end-ms 1769817600000 \
  --step-bars 4
```

`backtest` 不下载历史数据，只回放 SQLite 已保存的数据。有效时间点需要足够的 15m、1h、4h 历史输入，并且之后还要有完整的 96 根 15m 评估窗口。刚导入项目或只执行过一次 scan 时，`total_signals: 0` 很常见；这不代表命令故障。当前阶段没有额外的历史数据下载器，需要通过持续扫描积累数据，或者导入由同版本程序采集的 SQLite 数据库。

## 9. 手机复制命令技巧

- 在文档代码块上长按，选择“全选/复制”；
- 打开 Replit 的 Shell 工具，点输入区域后粘贴；
- 多行命令末尾的反斜杠必须是英文半角 `\`；
- 参数前必须是两个英文连字符 `--`，不能是长横线 `–` 或中文破折号；
- 如果粘贴多行失败，使用下面的单行格式：

```bash
PYTHONPATH=src python -m binance_ai_trader scan --database data/market_data.db --config config/universe.json --sectors-config config/sectors.json --kline-limit 200 --max-workers 5
```

使用键盘的代码/英文模式可避免自动替换引号和连字符。

## 10. Binance 403、451 或代理错误

先判断是代码问题还是网络出口问题：

```bash
python - <<'PY'
from urllib.request import urlopen
url = "https://fapi.binance.com/fapi/v1/ping"
with urlopen(url, timeout=15) as response:
    print(response.status, response.read().decode())
PY
```

处理顺序：

1. 如果返回 `200`，重新运行保守版 scan，并把 `--max-workers` 降为 `1`；
2. 如果返回 `403`、`451`、代理拒绝或连接超时，说明当前 Replit 网络出口、代理或所在地区无法访问该公共端点；
3. 稍后重试，并查看 Replit 状态页和 Binance 在所在地的可用性；
4. 不要添加 API Key——公开行情请求不需要 API Key，添加密钥也不能修复出口封锁；
5. 不要尝试绕过所在地法律、平台条款或网络限制；必要时在依法允许访问 Binance Futures 公共接口的本地或云端 Python 3.11+ 环境运行 scan；
6. 网络被阻断时，离线测试仍可运行；`regime`、`sectors`、`evaluate` 和 `backtest` 也可继续处理数据库中已有数据。

## 11. 常见错误

### `No module named binance_ai_trader`

原因：缺少 `PYTHONPATH=src`，或当前目录不是仓库根目录。

```bash
pwd
PYTHONPATH=src python -m binance_ai_trader --help
```

### `python: command not found`

确认 Replit 项目选择 Python 环境。其他平台可能只提供 `python3`；可先运行 `python3 --version`，但本项目脚本按 Replit Python 环境使用 `python`。

### `FileNotFoundError: config/...`

回到仓库根目录：

```bash
cd "$(git rev-parse --show-toplevel)"
```

### `database is locked`

停止其他正在运行的命令，等待数秒后重试。不要同时运行两个 scan 或在下载数据库时执行写操作。

### scan 没有信号输出

先看退出码和 Regime：

```bash
PYTHONPATH=src python -m binance_ai_trader regime --database data/market_data.db
echo $?
```

`BEAR`、`OBSERVE`、候选分数不足或风险收益规则不满足都会产生空输出，这是策略预期行为。

### evaluate 全部为零

数据库中可能没有 signals，或者生成信号后尚未采集到未来 15m K 线。继续按计划执行 scan 后再 evaluate。

### backtest 为零

数据库历史长度不足，或者可用时间点没有通过现有策略链路的信号。先检查数据库是否持续积累了 15m、1h、4h K 线；不要通过修改策略阈值来“制造”结果。

### 手机粘贴后出现 `unrecognized arguments`

删除参数前的横线并手动重新输入两个英文 `--`。最常见原因是手机把 `--help` 自动替换成 `–help`。

## P1 自动运行与 Telegram（可选）

### 环境变量

Telegram 默认关闭，且配置只从进程环境读取。不要把真实 token 写入 `.env.example`、Git
或日志：

```bash
export TELEGRAM_ENABLED=true
export TELEGRAM_BOT_TOKEN='你的 Telegram Bot token'
export TELEGRAM_CHAT_ID='目标 chat id'
```

连通性测试：

```bash
PYTHONPATH=src python -m binance_ai_trader telegram-test
```

返回值说明：

- `SENT`：Telegram 接受消息；
- `SKIPPED`：`TELEGRAM_ENABLED=false`，或 token/chat id 缺失；这是安全状态，不会让 Runner 退出；
- `FAILED`：Telegram 网络/API 请求失败，命令返回非零，但不影响 Binance 分析数据。

Telegram 凭据与 Binance 无关。本项目继续只读取 Binance 公共市场数据，不需要 Binance API
Key，也没有账户、余额、持仓或订单端点。

### 启动 Runner

```bash
PYTHONPATH=src python -m binance_ai_trader runner \
  --database data/market_data.db \
  --config config/universe.json \
  --sectors-config config/sectors.json \
  --history-days 180
```

兼容命令 `run-loop` 仍可使用。默认计划（UTC）：

- `scan`：每 15 分钟；
- `evaluate`：每 15 分钟；
- `paper-simulate`：每 15 分钟；
- `health`：每 15 分钟；
- `daily-report`：每天 00:05，并在 Telegram 启用时发送当日 Top 3；
- `collect-history`：每天 00:20，使用幂等写入继续补齐历史；
- `auto-research`：每 6 小时，候选仍不会自动进入生产扫描。

Runner 使用文件锁阻止双实例，并用 SQLite `runner_events` 判断任务是否到期。任务异常会记录
为 `FAILED`，但不会结束主进程或阻止其他任务。安全停止可使用 `Ctrl+C`；重新启动后会根据
数据库中最近的任务时间继续调度。

单次调度验证：

```bash
PYTHONPATH=src python -m binance_ai_trader runner \
  --database data/market_data.db \
  --once
```

### SQLite 健康检查

```bash
PYTHONPATH=src python -m binance_ai_trader health \
  --database data/market_data.db
```

输出为单行 JSON，包含：最近 scan、最近 Regime、最近信号数量、最近 Runner 错误、paper
equity、数据库总大小、`database_integrity` 和 `aggressive_allowed`。正常数据库的
`database_integrity` 应为 `ok`。健康检查不会调用 Binance，也不会发送订单。
