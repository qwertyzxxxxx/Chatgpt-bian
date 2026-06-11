# Replit 导入与运行指南（纯手机用户）

本项目是命令行分析工具，不是 Web Dashboard。所谓“部署到 Replit”是把 GitHub 仓库导入 Replit Workspace，在 Shell 中运行命令并将 SQLite 文件保存在项目工作区；不需要创建 Web Deployment，也不需要开放端口。

> Replit 界面名称可能更新。本文依据 Replit 官方的 GitHub Import、Workspace/Project Editor 和 Mobile App 文档编写；如果按钮名称略有变化，优先寻找 **Import**、**GitHub**、**Files** 和 **Shell**。

## 1. 准备

手机需要：

- Replit 账号；
- GitHub 仓库 URL；
- Replit 手机 App，或手机浏览器打开 `https://replit.com`；
- 仓库为私有时，需要把 Replit 连接到有访问权的 GitHub 账号。

Replit 官方说明，手机 App 支持在手机和平板上创建、编辑项目；GitHub guided import 同时支持公开和私有仓库。

## 2. 从 GitHub 导入

### 方式 A：公开仓库快速导入

假设 GitHub 地址是：

```text
https://github.com/OWNER/REPOSITORY
```

在手机浏览器地址栏打开：

```text
https://replit.com/github.com/OWNER/REPOSITORY
```

登录 Replit，确认导入。

### 方式 B：公开或私有仓库引导导入

1. 打开 `https://replit.com/import`；
2. 选择 **GitHub**；
3. 连接 GitHub 账号并授权仓库访问；
4. 选择目标仓库；
5. 点 **Import**；
6. 等待 Project Editor/Workspace 打开。

如果私有仓库不显示：在 Replit 的 Git Providers 设置中重新连接 GitHub；组织仓库还可能需要 GitHub 组织管理员批准 Replit OAuth/App 访问。

官方导入说明：

- https://docs.replit.com/getting-started/quickstarts/import-from-github
- https://docs.replit.com/references/workspace/import

## 3. 选择运行环境

选择 **Python** 项目/运行环境，Python 必须为 **3.11 或更高版本**。项目是标准库 Python CLI：

- 不需要 Node.js；
- 不需要 Web Server；
- 不需要 PostgreSQL；
- 不需要 Replit Secrets；
- 不需要 API Key；
- 不需要 Replit Deployment。

Replit 通常会根据 `pyproject.toml` 自动识别 Python。如果导入界面要求选择模板或语言，选择 **Python**。导入后在 Shell 验证：

```bash
python --version
```

## 4. 打开 Shell

在 Project Editor 中打开 **Tools / All tools**，选择 **Shell**。Replit Workspace 官方文档将 Shell 定义为与工作区交互的命令行工具；文件树可用于打开、重命名、删除和下载文件。

手机屏幕较小时：

1. 先关闭 Preview 或 Agent 面板；
2. 最大化 Shell 面板；
3. 横屏可以更容易查看 traceback；
4. 长按代码块复制，点 Shell 输入区域粘贴；
5. 确保参数使用英文 `--`。

Workspace 参考：

- https://docs.replit.com/replit-workspace/introduction-to-workspace
- https://docs.replit.com/references/platforms/mobile-app

## 5. 首次验证

确认位于仓库根目录：

```bash
pwd
find . -maxdepth 1 -type f -print
```

应看到 `pyproject.toml`、`README.md` 等文件。执行：

```bash
bash scripts/smoke_test.sh
```

或逐条运行：

```bash
python -m compileall -q src tests
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m binance_ai_trader --help
PYTHONPATH=src python -m binance_ai_trader scan --help
PYTHONPATH=src python -m binance_ai_trader backtest --help
```

测试不访问 Binance。全部通过后再执行真实 scan。

## 6. 运行真实 scan

复制整段到 Shell：

```bash
PYTHONPATH=src python -m binance_ai_trader scan \
  --database data/market_data.db \
  --config config/universe.json \
  --sectors-config config/sectors.json \
  --kline-limit 200 \
  --max-workers 5
```

第一次执行可能耗时较长，因为需要扫描符合条件的 USDT 永续合约并下载三个周期的 K 线。不要在同一个数据库上同时启动第二个 scan。

网络不稳定时使用：

```bash
PYTHONPATH=src python -m binance_ai_trader scan --database data/market_data.db --config config/universe.json --sectors-config config/sectors.json --kline-limit 200 --max-workers 1 --timeout 20 --max-retries 5
```

## 7. 运行其他命令

### Regime

```bash
PYTHONPATH=src python -m binance_ai_trader regime --database data/market_data.db
```

### Sectors

```bash
PYTHONPATH=src python -m binance_ai_trader sectors --database data/market_data.db --config config/sectors.json
```

### Evaluate

```bash
PYTHONPATH=src python -m binance_ai_trader evaluate --database data/market_data.db
```

### Backtest

```bash
PYTHONPATH=src python -m binance_ai_trader backtest --database data/market_data.db --config config/sectors.json --step-bars 1
```

这些命令的输入依赖：

- `regime`：需要已保存 BTC/ETH K 线；
- `sectors`：需要最新 scores 和 universe snapshot；
- `evaluate`：需要 signals 以及信号之后的未来 15m K 线；
- `backtest`：需要足够长的历史 K 线和完整的未来 96 根 15m 窗口。

详细语义见 `docs/runbook.md`。

## 8. SQLite 文件保存和下载

默认文件：

```text
data/market_data.db
```

Replit 项目保存在云端工作区。下载步骤：

1. 等待 scan/evaluate/backtest 结束；
2. 按 `docs/runbook.md` 执行 WAL checkpoint；
3. 打开左侧 **Files** 文件树；
4. 展开 `data`；
5. 找到 `market_data.db`；
6. 打开文件的操作菜单（通常是长按、右键或三点菜单）；
7. 选择 **Download**；
8. 在手机“文件/下载”目录确认文件存在且大小不为 0。

Replit Workspace 官方文档说明文件树支持下载文件。若手机 App 中看不到 Download，可用手机浏览器打开同一项目并切换为桌面站点，然后从 Files 菜单下载。

数据库没有提交到 Git，因为 `.gitignore` 排除了 `data/*.db`。重新从 GitHub 导入项目不会自动带回旧数据库；需要手动上传备份或继续使用原 Replit 项目。

## 9. 可选：设置 Run 按钮

本项目不要求配置 Run 按钮，Shell 是最明确的运行方式。如果 Replit 自动创建 Workflow，可以把 Run command 设置为：

```bash
bash scripts/smoke_test.sh
```

不要把 Run 按钮设置为持续 Web Server，也不要创建 Web Deployment。真实 scan 应在 Shell 中手动运行，以便看到日志和退出码。

## 10. `.env` 和 Secrets

仓库提供 `.env.example` 作为运行参数说明，但当前程序不会自动加载 `.env`，也没有必填 Secret。不要填写 Binance API Key、Secret Key、账户 Cookie 或订单凭证。

Replit 官方导入文档指出，现有 secret 值不会随 GitHub 导入；本项目不依赖它们，因此无需补录。

## 11. 403、代理或地区阻断

在 Shell 执行：

```bash
python - <<'PY'
from urllib.request import urlopen
with urlopen("https://fapi.binance.com/fapi/v1/ping", timeout=15) as response:
    print(response.status, response.read().decode())
PY
```

- 返回 `200`：使用 `--max-workers 1 --timeout 20 --max-retries 5` 重试 scan；
- 返回 `403`/`451`：Replit 出口、代理或所在地可能不允许访问 Binance Futures；
- 连接超时：稍后重试，并检查 Replit 状态；
- 不要添加 API Key，也不要绕过法律、地区限制或平台条款；
- 无法访问时，可在依法允许该公共接口的其他 Python 3.11+ 环境运行 scan，再把 SQLite 数据库上传到 Replit；
- 测试和已有 SQLite 数据上的 regime/sectors/evaluate/backtest 仍可使用。

## 12. 手机用户常见错误

### 粘贴后 `–help` 报错

手机把两个短横线 `--` 替换成了一个长横线 `–`。手动输入：

```bash
PYTHONPATH=src python -m binance_ai_trader --help
```

### 找不到 Shell

打开 Tools/All tools，搜索 `Shell`。手机 App 布局与浏览器可能不同，必要时使用浏览器桌面模式。

### 项目导入后自动进入 Agent

不要让 Agent重写项目。打开 Shell，直接运行 `bash scripts/smoke_test.sh`。本项目不需要生成 Web 页面。

### Python 版本过低

```bash
python --version
```

必须为 3.11+。重新创建/导入时选择 Python 环境，或在 Replit 项目配置中切换 Python runtime。

### 仓库或私有分支不可见

重新连接 GitHub；组织仓库需要确认 Replit 获得组织访问权限。也可以确认仓库 URL 和当前登录 GitHub 账号是否正确。

### 数据库下载后打不开

确保命令已结束并执行 WAL checkpoint，然后只下载主文件 `data/market_data.db`。文件大小为 0 时重新执行 scan 并检查日志。

### Replit 休眠或 Shell 中断

重新打开项目后，数据库文件仍应保存在工作区；再次执行命令即可。长时间回测建议提高 `--step-bars`（例如 `4` 或 `16`）减少评估点，但这会改变采样密度，不会改变策略规则。

## 13. 最小手机操作清单

按顺序逐行复制：

```bash
bash scripts/smoke_test.sh
```

```bash
PYTHONPATH=src python -m binance_ai_trader scan --database data/market_data.db --config config/universe.json --sectors-config config/sectors.json --kline-limit 200 --max-workers 5
```

```bash
PYTHONPATH=src python -m binance_ai_trader regime --database data/market_data.db
```

```bash
PYTHONPATH=src python -m binance_ai_trader sectors --database data/market_data.db --config config/sectors.json
```

```bash
PYTHONPATH=src python -m binance_ai_trader evaluate --database data/market_data.db
```

```bash
PYTHONPATH=src python -m binance_ai_trader backtest --database data/market_data.db --config config/sectors.json --step-bars 1
```
