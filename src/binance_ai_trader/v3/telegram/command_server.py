"""Telegram command server — interactive bot commands for system diagnostics.

Runs as a daemon thread, polling getUpdates every few seconds.
Dispatches /command messages to handler functions and replies inline.

Security: only responds to user IDs listed in TELEGRAM_ADMIN_USER_ID env var.
Safety:   all exceptions are caught at every level; never crashes run_server.py.

Available commands:
  /help    — show all commands
  /status  — task health check (last run times, recent errors)
  /market  — live market snapshot (top movers + filter pass/fail)
  /debug   — deep diagnosis (why no signals? market or bug?)
  /v4debug — V3 ranking diagnostic (pool/quality-sort/crowded-out symbols)
  /signals — last 10 pushed signals (V3 + V66) incl. live order manager action
  /orders  — current open paper orders + last 5 closed
  /perf    — paper trading performance (V3 + V66)
  /v66     — V66 watchlist pool status
  /limits  — view live dedup-window / max-open-orders settings
  /setlimit — adjust dedup-window / max-open-orders without redeploy
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

log = logging.getLogger(__name__)

_POLL_INTERVAL = 3.0
_COMMANDS = {
    "/help", "/status", "/market", "/debug", "/v4debug",
    "/signals", "/orders", "/perf", "/v66", "/portfolio",
    "/limits", "/setlimit",
    "/livestatus", "/livemode", "/setlive",
    "/paperon", "/paperoff", "/paperstatus",
    "/winrates", "/conditions", "/data",
}


def _ago(iso: str | None) -> str:
    """Return human-readable 'X min ago' string from ISO timestamp."""
    if not iso:
        return "从未"
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=UTC)
        delta = datetime.now(UTC) - ts
        secs = int(delta.total_seconds())
        if secs < 120:
            return f"{secs}秒前"
        if secs < 7200:
            return f"{secs // 60}分钟前"
        return f"{secs // 3600}小时前"
    except Exception:
        return iso[:16] if iso else "—"


def _fmt_price(v) -> str:
    try:
        d = Decimal(str(v))
        if d >= 1000:
            return f"{d:,.2f}"
        if d >= 1:
            return f"{d:.4f}"
        return f"{d:.6f}"
    except Exception:
        return str(v)


def _fmt_vol(v) -> str:
    try:
        f = float(v)
        if f >= 1e9:
            return f"{f/1e9:.1f}B"
        if f >= 1e6:
            return f"{f/1e6:.0f}M"
        if f >= 1e3:
            return f"{f/1e3:.0f}K"
        return str(int(f))
    except Exception:
        return str(v)


# ─────────────────────────────────────────────────────────────────────────────
# Command handlers — each returns a plain-text string
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_help() -> str:
    return (
        "📖 可用指令\n"
        "━━━━━━━━━━━━━━\n"
        "/status  🩺 系统健康检查\n"
        "/market  📊 当前行情快照\n"
        "/debug   🔧 为何无信号（深度诊断）\n"
        "/v4debug 🎯 V3排序诊断（候选池/质量排序/被挤掉币种）\n"
        "/signals 📋 最近10条推送信号（含实盘订单管理动作）\n"
        "/orders  📂 当前模拟单 + 最近成交\n"
        "/perf    🏆 模拟盘绩效统计\n"
        "/winrates 📊 V66/V663/V664 策略胜率横向对比\n"
        "/conditions [v66|v663|v664] 📋 策略过滤条件详情\n"
        "/data    🔌 生产只读数据API接口说明\n"
        "/portfolio [v3|v66|rev] 📊 完整持仓报告（默认v3）\n"
        "/v66     📡 V66 监控池状态\n"
        "/limits  ⚙️ 查看去重窗口/持仓上限设置\n"
        "/setlimit ⚙️ 调整去重窗口/持仓上限\n"
        "/livestatus 🟢 查看实盘开关/仓位状态\n"
        "/livemode   🔌 开关某策略实盘交易\n"
        "/setlive    💰 调整实盘仓位大小(USDT)\n"
        "/paperstatus 📋 查看模拟扫描策略开关状态\n"
        "/paperon  <策略> ▶️ 开启模拟扫描（如 /paperon sma120）\n"
        "/paperoff <策略> ⏸ 暂停模拟扫描（如 /paperoff sma120）\n"
        "/help    📖 显示此帮助"
    )


def _cmd_limits() -> str:
    from binance_ai_trader.v3.settings.repository import (
        DEFAULTS, V3_STRATEGY_ID, V66_STRATEGY_ID, VALID_DEDUP_HOURS,
        V3RuntimeSettingsRepository,
    )

    repo = V3RuntimeSettingsRepository()
    lines = ["⚙️ 去重/持仓限制设置", "━━━━━━━━━━━━━━"]
    for alias, strategy_id in (("v3", V3_STRATEGY_ID), ("v66", V66_STRATEGY_ID)):
        s = repo.get(strategy_id)
        defaults = DEFAULTS[strategy_id]
        dedup_hours = s.dedup_hours if s.dedup_hours is not None else defaults["dedup_hours"]
        max_orders = s.max_open_orders if s.max_open_orders is not None else defaults["max_open_orders"]
        dedup_tag = "（默认）" if s.dedup_hours is None else f"（已调整，{_ago(s.updated_at)}）"
        orders_tag = "（默认）" if s.max_open_orders is None else f"（已调整，{_ago(s.updated_at)}）"
        lines.append(f"\n【{alias}】")
        lines.append(f"去重窗口: {dedup_hours}h {dedup_tag}")
        lines.append(f"持仓上限: {max_orders} {orders_tag}")
    lines.append("\n用法: /setlimit v3 dedup 12")
    lines.append("      /setlimit v66 maxorders 8")
    lines.append("      /setlimit v3 reset  (恢复默认)")
    lines.append(f"去重窗口可选值: {sorted(VALID_DEDUP_HOURS)}")
    return "\n".join(lines)


def _cmd_setlimit(args: list[str], user_id: int | None) -> str:
    from binance_ai_trader.v3.settings.repository import (
        STRATEGY_ALIASES, V3RuntimeSettingsRepository,
    )

    if len(args) < 2:
        return (
            "❌ 用法: /setlimit <v3|v66> <dedup|maxorders> <数值>\n"
            "      /setlimit <v3|v66> reset\n"
            "示例: /setlimit v3 dedup 12\n"
            "      /setlimit v66 maxorders 8"
        )

    alias = args[0].lower()
    strategy_id = STRATEGY_ALIASES.get(alias)
    if strategy_id is None:
        return f"❌ 未知策略 '{alias}'，可选: {', '.join(STRATEGY_ALIASES)}"

    repo = V3RuntimeSettingsRepository()
    field = args[1].lower()
    updated_by = str(user_id) if user_id is not None else None

    try:
        if field == "reset":
            repo.reset(strategy_id)
            return f"✅ {alias} 的去重/持仓设置已恢复默认值"

        if len(args) < 3:
            return "❌ 缺少数值，例如: /setlimit v3 dedup 12"
        value = int(args[2])

        if field in ("dedup", "dedup_hours"):
            repo.set_dedup_hours(strategy_id, value, updated_by=updated_by)
            return f"✅ {alias} 去重窗口已设为 {value}h"
        if field in ("maxorders", "max_orders", "max_open_orders"):
            repo.set_max_open_orders(strategy_id, value, updated_by=updated_by)
            return f"✅ {alias} 持仓上限已设为 {value}"
        return f"❌ 未知字段 '{field}'，可选: dedup / maxorders / reset"
    except ValueError as exc:
        return f"❌ {exc}"


def _cmd_livestatus() -> str:
    from binance_ai_trader.v3.settings.repository import (
        LIVE_DEFAULTS, STRATEGY_ALIASES, V3RuntimeSettingsRepository,
    )

    repo = V3RuntimeSettingsRepository()
    master_on = os.environ.get("LIVE_TRADING_ENABLED", "").lower() == "true"
    lines = ["🟢 实盘交易状态", "━━━━━━━━━━━━━━"]
    lines.append(f"全局开关(LIVE_TRADING_ENABLED): {'✅ ON' if master_on else '⛔ OFF（优先级最高，覆盖下方策略开关）'}")

    id_to_alias = {v: k for k, v in STRATEGY_ALIASES.items()}
    for strategy_id in LIVE_DEFAULTS:
        alias = id_to_alias.get(strategy_id, strategy_id)
        on, notional = repo.resolve_live(strategy_id)
        s = repo.get(strategy_id)
        on_tag = "（默认）" if s.live_enabled is None else f"（已调整，{_ago(s.updated_at)}）"
        notional_tag = "（默认）" if s.notional_usdt is None else f"（已调整，{_ago(s.updated_at)}）"
        effective = "✅ 实盘中" if (master_on and on) else "⛔ 未实盘（仅模拟）"
        lines.append(f"\n【{alias}】{effective}")
        lines.append(f"策略开关: {'ON' if on else 'OFF'} {on_tag}")
        lines.append(f"仓位大小: {notional} USDT {notional_tag}")

    all_aliases = "|".join(STRATEGY_ALIASES.keys())
    lines.append(f"\n用法: /livemode <{all_aliases}> on|off")
    lines.append(f"      /setlive <{all_aliases}> <USDT>")
    return "\n".join(lines)


def _cmd_livemode(args: list[str], user_id: int | None) -> str:
    from binance_ai_trader.v3.settings.repository import (
        STRATEGY_ALIASES, V3RuntimeSettingsRepository,
    )

    all_aliases = "|".join(STRATEGY_ALIASES.keys())
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        return f"❌ 用法: /livemode <{all_aliases}> <on|off>\n示例: /livemode v663 on"

    alias = args[0].lower()
    strategy_id = STRATEGY_ALIASES.get(alias)
    if strategy_id is None:
        return f"❌ 未知策略 '{alias}'，可选: {all_aliases}"

    enabled = args[1].lower() == "on"
    updated_by = str(user_id) if user_id is not None else None
    repo = V3RuntimeSettingsRepository()
    repo.set_live_enabled(strategy_id, enabled, updated_by=updated_by)

    master_on = os.environ.get("LIVE_TRADING_ENABLED", "").lower() == "true"
    warn = "" if master_on else "\n⚠️ 注意: 全局开关 LIVE_TRADING_ENABLED 当前为 OFF，此设置暂不会实际生效"
    return f"✅ {alias} 实盘交易已设为 {'ON' if enabled else 'OFF'}{warn}"


def _cmd_paperstatus() -> str:
    from binance_ai_trader.v3.settings.repository import (
        PAPER_ONLY_STRATEGIES, STRATEGY_ALIASES, V3RuntimeSettingsRepository,
    )

    repo = V3RuntimeSettingsRepository()
    id_to_alias = {v: k for k, v in STRATEGY_ALIASES.items()}
    lines = ["📋 模拟扫描策略开关状态", "━━━━━━━━━━━━━━"]
    for strategy_id in sorted(PAPER_ONLY_STRATEGIES):
        alias = id_to_alias.get(strategy_id, strategy_id)
        s = repo.get(strategy_id)
        # None = default ON; False = explicitly paused
        active = s.live_enabled is not False
        tag = "（默认）" if s.live_enabled is None else f"（已调整，{_ago(s.updated_at)}）"
        status = "▶️ 扫描中" if active else "⏸ 已暂停"
        lines.append(f"【{alias}】{status} {tag}")
    lines.append("\n用法: /paperon sma120  /paperoff sma120")
    return "\n".join(lines)


def _cmd_paperon(args: list[str], user_id: int | None) -> str:
    from binance_ai_trader.v3.settings.repository import (
        PAPER_ONLY_STRATEGIES, STRATEGY_ALIASES, V3RuntimeSettingsRepository,
    )

    if not args:
        aliases = [k for k, v in STRATEGY_ALIASES.items() if v in PAPER_ONLY_STRATEGIES]
        return f"❌ 用法: /paperon <策略>\n可选策略: {', '.join(aliases)}"

    alias = args[0].lower()
    strategy_id = STRATEGY_ALIASES.get(alias)
    if strategy_id is None or strategy_id not in PAPER_ONLY_STRATEGIES:
        aliases = [k for k, v in STRATEGY_ALIASES.items() if v in PAPER_ONLY_STRATEGIES]
        return f"❌ 未知策略 '{alias}'，可选: {', '.join(aliases)}"

    updated_by = str(user_id) if user_id is not None else None
    V3RuntimeSettingsRepository().set_live_enabled(strategy_id, True, updated_by=updated_by)
    return f"✅ {alias} 模拟扫描已开启 ▶️"


def _cmd_paperoff(args: list[str], user_id: int | None) -> str:
    from binance_ai_trader.v3.settings.repository import (
        PAPER_ONLY_STRATEGIES, STRATEGY_ALIASES, V3RuntimeSettingsRepository,
    )

    if not args:
        aliases = [k for k, v in STRATEGY_ALIASES.items() if v in PAPER_ONLY_STRATEGIES]
        return f"❌ 用法: /paperoff <策略>\n可选策略: {', '.join(aliases)}"

    alias = args[0].lower()
    strategy_id = STRATEGY_ALIASES.get(alias)
    if strategy_id is None or strategy_id not in PAPER_ONLY_STRATEGIES:
        aliases = [k for k, v in STRATEGY_ALIASES.items() if v in PAPER_ONLY_STRATEGIES]
        return f"❌ 未知策略 '{alias}'，可选: {', '.join(aliases)}"

    updated_by = str(user_id) if user_id is not None else None
    V3RuntimeSettingsRepository().set_live_enabled(strategy_id, False, updated_by=updated_by)
    return f"⏸ {alias} 模拟扫描已暂停（结算/报告任务继续运行）"


def _cmd_setlive(args: list[str], user_id: int | None) -> str:
    from binance_ai_trader.v3.settings.repository import (
        STRATEGY_ALIASES, V3RuntimeSettingsRepository,
    )

    all_aliases = "|".join(STRATEGY_ALIASES.keys())
    if len(args) < 2:
        return f"❌ 用法: /setlive <{all_aliases}> <数值USDT>\n示例: /setlive v663 2000"

    alias = args[0].lower()
    strategy_id = STRATEGY_ALIASES.get(alias)
    if strategy_id is None:
        return f"❌ 未知策略 '{alias}'，可选: {all_aliases}"

    updated_by = str(user_id) if user_id is not None else None
    repo = V3RuntimeSettingsRepository()
    try:
        repo.set_notional_usdt(strategy_id, args[1], updated_by=updated_by)
        return f"✅ {alias} 仓位大小已设为 {args[1]} USDT"
    except ValueError as exc:
        return f"❌ {exc}"
    except Exception as exc:
        return f"❌ 数值无效: {exc}"


def _cmd_status(db_path: Path) -> str:
    lines = ["🩺 系统健康检查", "━━━━━━━━━━━━━━"]

    # PostgreSQL connectivity
    try:
        from binance_ai_trader.v3.storage.pg import get_conn
        conn = get_conn()
        conn.close()
        lines.append("PostgreSQL     ✅ 正常")
    except Exception as exc:
        lines.append(f"PostgreSQL     ❌ {exc}")

    # Binance API
    try:
        from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
        BinancePublicClient().tickers_24h()
        lines.append("Binance API    ✅ 正常")
    except Exception as exc:
        lines.append(f"Binance API    ❌ {exc}")

    # Task run times from SQLite runner_events
    lines.append("")
    task_groups = {
        "V3 任务": ["v3_hotlist_scan", "v3_paper_settle", "v3_shadow_report"],
        "V66 任务": ["v66_scan", "v66_settle", "v66_report"],
        "其他": ["v3_live_sync", "v3_health_check"],
    }
    try:
        from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
        repo = MarketDataRepository(db_path)
        for group, task_ids in task_groups.items():
            summaries = {}
            for tid in task_ids:
                summaries[tid] = repo.load_runner_task_summary(tid)
            if any(v is not None for v in summaries.values()):
                lines.append(f"[{group}]")
                for tid, s in summaries.items():
                    if s is None:
                        continue
                    status_icon = "✅" if s["status"] == "SUCCEEDED" else "❌"
                    lines.append(f"  {tid:<22} {_ago(s['started_at'])}  {status_icon}")
        # Recent errors
        err = repo.load_latest_runner_error()
        if err:
            lines.append(f"\n⚠️  最近错误: {err['event_type']} @ {_ago(err['started_at'])}")
            lines.append(f"   {str(err.get('error_message',''))[:80]}")
        repo.close()
    except Exception as exc:
        lines.append(f"[任务状态查询失败: {exc}]")

    return "\n".join(lines)


def _cmd_market(universe_config) -> str:
    from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
    client = BinancePublicClient()
    tickers = client.tickers_24h()
    info = client.exchange_info()

    valid_syms = {
        item.symbol
        for item in info
        if item.quote_asset == "USDT"
        and item.margin_asset == "USDT"
        and item.contract_type == "PERPETUAL"
        and item.status == "TRADING"
        and item.base_asset not in universe_config.stablecoin_base_assets
        and item.symbol not in universe_config.denied_symbols
        and not item.base_asset.endswith(universe_config.leveraged_token_suffixes)
    }

    min_vol = Decimal("5_000_000")
    eligible = [t for t in tickers if t.symbol in valid_syms and t.quote_volume >= min_vol]
    gainers = sorted(eligible, key=lambda t: -t.price_change_percent)[:6]
    losers  = sorted(eligible, key=lambda t:  t.price_change_percent)[:6]

    v3_eligible   = [t for t in eligible if abs(t.price_change_percent) >= 15]
    v66_eligible  = eligible  # no move requirement

    lines = ["📊 当前行情快照", "━━━━━━━━━━━━━━"]
    lines.append("涨幅榜 TOP6 (成交量≥500万):")
    for t in gainers:
        pct = float(t.price_change_percent)
        flag = "✅" if pct >= 15 else "  "
        lines.append(f"  {flag}{t.symbol:<14} {pct:+.1f}%  {_fmt_vol(t.quote_volume)}")
    lines.append("跌幅榜 TOP6:")
    for t in losers:
        pct = float(t.price_change_percent)
        flag = "✅" if pct <= -15 else "  "
        lines.append(f"  {flag}{t.symbol:<14} {pct:+.1f}%  {_fmt_vol(t.quote_volume)}")
    lines.append("")
    lines.append(f"[V3] ≥15%涨跌 + 成交量≥500万: {len(v3_eligible)} 个币")
    lines.append(f"[V66] 成交量≥500万 (无涨跌要求): {len(v66_eligible)} 个币")
    if len(v3_eligible) == 0:
        lines.append("⚠️  V3 无符合条件行情 → 正常无信号")
    return "\n".join(lines)


def _cmd_debug(universe_config) -> str:
    from binance_ai_trader.v3.candidates.repository import V3CandidateRepository
    from binance_ai_trader.infrastructure.binance_public import BinancePublicClient

    lines = ["🔧 深度诊断 — 为何无信号", "━━━━━━━━━━━━━━"]

    # Recent candidates from DB
    try:
        repo = V3CandidateRepository(None)
        recent = repo.load_recent(hours=24)
        pushed   = [c for c in recent if c.status == "PUSHED"]
        blocked  = [c for c in recent if c.status == "BLOCKED"]
        deduped  = [c for c in recent if c.status == "DEDUP"]
        v3_all   = [c for c in recent if c.strategy_id == "hotlist_momentum_v3"]
        v66_all  = [c for c in recent if c.strategy_id == "hotlist_v66"]

        lines.append(f"[过去24h 候选统计]")
        lines.append(f"  V3  扫描: {len(v3_all)}  推送: {len([c for c in v3_all if c.status=='PUSHED'])}  "
                     f"风控拦: {len([c for c in v3_all if c.status=='BLOCKED'])}  去重: {len([c for c in v3_all if c.status=='DEDUP'])}")
        lines.append(f"  V66 扫描: {len(v66_all)}  推送: {len([c for c in v66_all if c.status=='PUSHED'])}  "
                     f"风控拦: {len([c for c in v66_all if c.status=='BLOCKED'])}  去重: {len([c for c in v66_all if c.status=='DEDUP'])}")

        if blocked:
            lines.append("")
            lines.append(f"[被风控拦截 — 最近10条, 实际拦截原因]")
            for c in sorted(blocked, key=lambda x: x.created_at or "", reverse=True)[:10]:
                reason = c.reason or "未知原因"
                lines.append(f"  {c.symbol:<14} {c.direction:<5} {reason}  {_ago(c.created_at)}")
        else:
            lines.append("")
            lines.append("过去24h 无被拦截记录（可能扫描未生成候选）")
    except Exception as exc:
        lines.append(f"[DB查询失败: {exc}]")

    # Live market check
    lines.append("")
    lines.append("[当前行情 — TOP5 涨跌幅]")
    try:
        client = BinancePublicClient()
        tickers = client.tickers_24h()
        info = client.exchange_info()
        valid = {
            i.symbol for i in info
            if i.quote_asset == "USDT" and i.margin_asset == "USDT"
            and i.contract_type == "PERPETUAL" and i.status == "TRADING"
            and i.base_asset not in universe_config.stablecoin_base_assets
            and i.symbol not in universe_config.denied_symbols
        }
        eligible = sorted(
            [t for t in tickers if t.symbol in valid and t.quote_volume >= Decimal("5000000")],
            key=lambda t: -abs(float(t.price_change_percent))
        )[:10]
        for t in eligible:
            pct = float(t.price_change_percent)
            v3ok = "✅V3" if abs(pct) >= 15 else "  "
            lines.append(f"  {v3ok} {t.symbol:<14} {pct:+.1f}%  {_fmt_vol(t.quote_volume)}")
    except Exception as exc:
        lines.append(f"  [获取行情失败: {exc}]")

    return "\n".join(lines)


def _cmd_v4debug() -> str:
    from binance_ai_trader.v3.debug.repository import ScanDebugRepository

    snap = ScanDebugRepository().load("hotlist_momentum_v3")
    if snap is None:
        return "🎯 V3排序诊断\n━━━━━━━━━━━━━━\n暂无数据（等待下次扫描）"

    lines = ["🎯 V3排序诊断", "━━━━━━━━━━━━━━"]
    lines.append(f"扫描时间: {_ago(snap.created_at)}")
    lines.append(f"候选池(|24h涨跌|≥15%+量≥500万, 取前15): {snap.pool_size} 个")
    lines.append(f"成功计算入场/止损/止盈: {snap.computed_count} 个")
    lines.append(f"其中可实盘(止损≤8%): {snap.live_eligible_count} 个")

    lines.append("")
    lines.append("[质量排序 Top10 — 实盘优先>止损小>成交额大>1h趋势一致>涨跌幅兜底]")
    if not snap.top10:
        lines.append("  （无合格候选）")
    for i, row in enumerate(snap.top10, 1):
        mark = "✅入选" if row.get("selected") else "  "
        live = "🟢实盘可" if row.get("live_eligible") else "⚪仅纸面"
        trend = "↗趋势一致" if row.get("trend_aligned") else "↘趋势背离"
        lines.append(
            f"{i:2d}. {mark} {row['symbol']:<14} {row['direction']:<5} "
            f"止损:{row['stop_pct']:.1f}%  {live}  {trend}  "
            f"24h:{row['change_24h']:+.1f}%  量:{_fmt_vol(row['quote_volume'])}"
        )

    lines.append("")
    lines.append("[被极端涨跌幅挤掉的币种 — 质量尚可但未进最终3席]")
    if not snap.crowded_out:
        lines.append("  （无 — 候选池全部进入最终名单，或候选不足3个）")
    else:
        for row in snap.crowded_out:
            live = "🟢实盘可" if row.get("live_eligible") else "⚪仅纸面"
            lines.append(
                f"  {row['symbol']:<14} {row['direction']:<5} "
                f"止损:{row['stop_pct']:.1f}%  {live}  24h:{row['change_24h']:+.1f}%"
            )

    return "\n".join(lines)


def _cmd_signals() -> str:
    from binance_ai_trader.v3.candidates.repository import V3CandidateRepository
    repo = V3CandidateRepository(None)
    candidates = repo.load_recent(hours=72)
    pushed = [c for c in candidates if c.status == "PUSHED"]
    pushed.sort(key=lambda c: c.created_at or "", reverse=True)
    pushed = pushed[:10]

    if not pushed:
        return "📋 最近72h 无推送信号"

    from binance_ai_trader.v3.live.repository import LiveOrderRepository
    live_repo = LiveOrderRepository()

    _ACTION_LABEL = {
        "PENDING": "🟡实盘挂单中",
        "FILLED": "🟢实盘已成交",
        "CLOSED_TP": "✅实盘止盈平仓",
        "CLOSED_SL": "❌实盘止损平仓",
        "CANCELED": "⚪实盘已撤销",
        "REJECTED": "🚫实盘被拒",
        "REPLACED": "🔁实盘旧单已替换",
        "CANCELED_EXPIRED": "⏰实盘挂单超时撤销",
        "CANCELED_CONFLICT": "⚠️实盘因冲突撤销",
        "IGNORED_DUPLICATE": "🔂实盘忽略(重复信号)",
        "IGNORED_WORSE_ENTRY": "🔂实盘忽略(入场价更差)",
        "DIRECTION_CONFLICT": "⚠️实盘方向冲突",
        "POSITION_EXISTS_SAME_SIDE": "⚠️实盘已有同向持仓",
        "POSITION_EXISTS_OPPOSITE_SIDE": "⚠️实盘已有反向持仓",
    }

    lines = [f"📋 最近推送信号 ({len(pushed)}条)", "━━━━━━━━━━━━━━"]
    for i, c in enumerate(pushed, 1):
        strat = "V3" if c.strategy_id == "hotlist_momentum_v3" else "V66"
        rr = f"RR:{c.rr}" if c.rr else ""
        stop = f"止损:{c.stop_pct:.1f}%" if c.stop_pct else ""
        block = (
            f"{i}. [{strat}] {c.signal_id or '—'}\n"
            f"   {c.symbol} {c.direction}  {_ago(c.created_at)}\n"
            f"   Entry:{_fmt_price(c.entry)}  {stop}  {rr}"
        )
        if c.signal_id:
            try:
                live = live_repo.load_by_signal_id(c.signal_id)
            except Exception:
                live = None
            if live is not None:
                label = _ACTION_LABEL.get(live.status, live.status)
                block += f"\n   {label}"
                if live.reject_reason:
                    block += f"\n   原因: {live.reject_reason}"
        lines.append(block)
    return "\n".join(lines)


def _cmd_orders() -> str:
    from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
    repo = V3PaperOrderRepository()
    open_orders = repo.load_open()
    closed = repo.load_recent_settled(n=5)

    lines = ["📂 模拟单状态", "━━━━━━━━━━━━━━"]

    if not open_orders:
        lines.append("[当前无开仓单]")
    else:
        lines.append(f"[开仓中 — {len(open_orders)}单]")
        _STRAT_TAG = {"hotlist_momentum_v3": "V3", "hotlist_v66": "V66", "hotlist_reversal": "REV", "hotlist_v662": "V662", "hotlist_v663": "V663", "hotlist_v664": "V664", "wave_long": "W↑", "wave_short": "W↓"}
        for o in open_orders:
            strat = _STRAT_TAG.get(o.strategy_id, o.strategy_id[:4])
            sl_pct = abs(Decimal(o.entry) - o.stop_loss) / Decimal(o.entry) * 100
            lines.append(
                f"[{strat}] {o.symbol} {o.direction}  {o.status}\n"
                f"  Entry:{_fmt_price(o.entry)}  SL:{_fmt_price(o.stop_loss)}({sl_pct:.1f}%)  "
                f"TP1:{_fmt_price(o.tp1)}\n"
                f"  建仓:{_ago(o.created_at)}"
            )

    if closed:
        lines.append(f"\n[最近结算 — {len(closed)}条]")
        _RESULT_ICON = {"TP1": "✅", "TP2": "✅", "SL": "❌", "TIMEOUT": "⏰"}
        for o in closed:
            icon = _RESULT_ICON.get(o.result or "", "📋")
            pnl = f"{float(o.pnl_pct):+.2f}%" if o.pnl_pct else ""
            lines.append(f"  {icon} {o.symbol} {o.direction} {o.result}  {pnl}  {_ago(o.closed_at)}")

    return "\n".join(lines)


def _cmd_perf() -> str:
    from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
    repo = V3PaperOrderRepository()
    all_orders = repo.load_all()

    def _stats(orders):
        closed = [o for o in orders if o.status == "CLOSED" and o.result in ("TP1", "TP2", "SL", "TIMEOUT")]
        if not closed:
            return None
        wins    = [o for o in closed if o.result in ("TP1", "TP2")]
        filled  = [o for o in orders if o.status in ("FILLED", "CLOSED") and o.filled_at]
        pnls    = [float(o.pnl_pct) for o in closed if o.pnl_pct]
        open_n  = len([o for o in orders if o.status in ("OPEN", "FILLED")])
        return {
            "total":      len(orders),
            "closed":     len(closed),
            "open":       open_n,
            "win_rate":   len(wins) / len(closed) * 100 if closed else 0,
            "tp1_rate":   len([o for o in closed if o.result == "TP1"]) / len(closed) * 100 if closed else 0,
            "fill_rate":  len(filled) / len(orders) * 100 if orders else 0,
            "avg_pnl":    sum(pnls) / len(pnls) if pnls else 0,
        }

    lines = ["🏆 模拟盘绩效", "━━━━━━━━━━━━━━"]

    for strat_id, label in [("hotlist_momentum_v3", "V3"), ("hotlist_v66", "V66"), ("hotlist_reversal", "REV"), ("hotlist_v662", "V662"), ("hotlist_v663", "V663"), ("hotlist_v664", "V664"), ("wave_long", "W↑"), ("wave_short", "W↓")]:
        orders = [o for o in all_orders if o.strategy_id == strat_id]
        s = _stats(orders)
        lines.append(f"[{label} — {strat_id}]")
        if s is None:
            lines.append("  暂无数据")
            continue
        lines.append(f"  总信号: {s['total']}  开仓率: {s['fill_rate']:.0f}%  当前开仓: {s['open']}")
        lines.append(f"  胜率:   {s['win_rate']:.0f}%  TP1率: {s['tp1_rate']:.0f}%")
        lines.append(f"  已结算: {s['closed']}  平均收益: {s['avg_pnl']:+.2f}%")

    return "\n".join(lines)


_PORTFOLIO_STRATEGIES = {
    "v3": "hotlist_momentum_v3",
    "v66": "hotlist_v66",
    "rev": "hotlist_reversal",
    "reversal": "hotlist_reversal",
    "v662": "hotlist_v662",
    "v663": "hotlist_v663",
    "v664": "hotlist_v664",
    "wl":         "wave_long",
    "ws":         "wave_short",
    "wave_long":  "wave_long",
    "wave_short": "wave_short",
}


def _cmd_portfolio(args: list[str], notifier, db_path: Path) -> str:
    """On-demand full shadow-report (same format as the hourly push) for any strategy.

    Usage: /portfolio [v3|v66|rev]  (default: v3)
    """
    from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
    from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
    from binance_ai_trader.v3.performance.calculator import V3PerformanceCalculator
    from binance_ai_trader.v3.telegram.shadow_report import V3ShadowReporter

    key = (args[0].lower() if args else "v3")
    strategy_id = _PORTFOLIO_STRATEGIES.get(key)
    if strategy_id is None:
        return f"❓ 未知策略 '{args[0] if args else ''}'，可用: v3 / v66 / rev"

    order_repo = V3PaperOrderRepository()
    perf_calc = V3PerformanceCalculator(order_repo)
    try:
        client = BinancePublicClient()
    except Exception:
        client = None

    reporter = V3ShadowReporter(notifier, order_repo, perf_calc, strategy_id, client=client)
    return reporter._build_message()


_STRATEGY_CONDITIONS: dict[str, dict] = {
    "v66": {
        "label": "V66",
        "id":    "hotlist_v66",
        "lines": [
            "宇宙: USDT永续合约 | 成交量≥500万USDT",
            "选币: 涨幅榜Top6 + 跌幅榜Top6（无24h涨跌幅下限）",
            "监控: 观测窗口120min，每15min刷新，最多3信号/次",
            "入场: 15m EMA20 ∓0.25×ATR 限价挂单",
            "止损: 近20根K最低/最高 ∓1×ATR | ≤5%",
            "止盈: TP1=1R  TP2=2R | RR≥2",
            "趋势: ❌ 无过滤（V1风格，宽松进场）",
            "量比: ❌ 无要求",
            "TTL: 60min 限价单超时未成交放弃",
            "★ 新增: min_stop_pct=1.5%（止损<1.5%不入场）",
        ],
    },
    "v662": {
        "label": "V662",
        "id":    "hotlist_v662",
        "lines": [
            "宇宙: USDT永续合约 | 成交量≥500万USDT",
            "选币: 涨幅榜Top6 + 跌幅榜Top6 | |24h涨跌|≥5%",
            "监控: 观测窗口60min，每15min刷新，最多3信号/次",
            "入场: 15m EMA20 ∓0.25×ATR 限价挂单",
            "止损: 近20根K最低/最高 ∓1×ATR | ≤3%",
            "止盈: TP1=1R  TP2=2R | RR≥2",
            "趋势: 1h 价格在EMA20上方(多)/下方(空)",
            "趋势: 4h 价格在EMA20上方(多)/下方(空)",
            "量比: ≥1.2x（放量确认）",
            "TTL: 90min 限价单超时未成交放弃",
            "★ 新增: min_stop_pct=1.5%",
        ],
    },
    "v663": {
        "label": "V663",
        "id":    "hotlist_v663",
        "lines": [
            "宇宙: USDT永续合约 | 成交量≥500万USDT",
            "选币: 涨幅榜Top6 + 跌幅榜Top6 | |24h涨跌|≥5%",
            "监控: 观测窗口60min，每15min刷新，最多3信号/次",
            "入场: 15m EMA20 ∓0.25×ATR 限价挂单",
            "止损: 近20根K最低/最高 ∓1×ATR | ≤3%",
            "止盈: TP1=1R  TP2=2R | RR≥2",
            "趋势: 1h EMA10>EMA20>EMA50 三线排列（多头/空头反向）",
            "趋势: 4h EMA10>EMA20>EMA50 三线排列（同上）",
            "量比: ≥1.2x（放量确认）",
            "TTL: 90min 限价单超时未成交放弃",
            "★ 新增: min_stop_pct=1.5%",
            "升级vs V662: 三线排列 替代 简单价格位置",
        ],
    },
    "v664": {
        "label": "V664",
        "id":    "hotlist_v664",
        "lines": [
            "宇宙: USDT永续合约 | 成交量≥500万USDT",
            "选币: 涨幅榜Top6 + 跌幅榜Top6 | |24h涨跌|≥5%",
            "监控: 观测窗口480min(8h)，每15min刷新，最多3信号/次",
            "入场: 当前价在15m EMA20 ±1.5% 以内（精准回踩到位）",
            "止损: 近20根K最低/最高 ∓1×ATR | ≤2.5%",
            "止盈: 目标TP2=2R（直接打2R，不止盈TP1）",
            "趋势: 1h + 4h EMA10>EMA20>EMA50 三线排列",
            "量缩: 量比<1.0（回踩时逆势力量弱）",
            "方向: 🔴 仅做多LONG（SHORT历史胜率45.5%已禁用）",
            "TTL: 60min 限价单超时",
            "升级vs V663: 精准等回踩 + 量缩 + 更紧止损",
        ],
    },
}


def _cmd_conditions(args: list[str]) -> str:
    key = (args[0].lower() if args else "").replace("hotlist_", "")
    if key and key not in _STRATEGY_CONDITIONS:
        valid = " / ".join(_STRATEGY_CONDITIONS.keys())
        return f"❓ 未知策略 '{key}'，可选: {valid}\n用法: /conditions v663"

    targets = ([key] if key else list(_STRATEGY_CONDITIONS.keys()))
    lines   = ["📋 策略过滤条件", "━━━━━━━━━━━━━━"]
    for k in targets:
        cfg = _STRATEGY_CONDITIONS[k]
        lines.append(f"\n【{cfg['label']}】({cfg['id']})")
        for i, cond in enumerate(cfg["lines"], 1):
            lines.append(f"  {i:2d}. {cond}")
    lines.append("\n用法: /conditions v663  /conditions v664")
    return "\n".join(lines)


def _cmd_winrates() -> str:
    from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
    from datetime import UTC, datetime
    repo       = V3PaperOrderRepository()
    all_orders = repo.load_all()
    today_str  = datetime.now(UTC).strftime("%Y-%m-%d")

    STRATEGIES = [
        ("hotlist_v66",  "V66"),
        ("hotlist_v662", "V662"),
        ("hotlist_v663", "V663"),
        ("hotlist_v664", "V664"),
    ]

    def _row(orders):
        closed  = [o for o in orders if o.status == "CLOSED"
                   and o.result in ("TP1", "TP2", "SL", "TIMEOUT")]
        tp1     = sum(1 for o in closed if o.result in ("TP1", "TP2"))
        sl      = sum(1 for o in closed if o.result == "SL")
        timeout = sum(1 for o in closed if o.result == "TIMEOUT")
        pushed  = sum(1 for o in orders if o.pushed)
        filled  = sum(1 for o in orders if o.filled_at)
        denom   = tp1 + sl
        wr      = tp1 / denom * 100 if denom else 0
        pnls    = [float(o.pnl_pct) for o in closed
                   if o.pnl_pct and o.result in ("TP1", "SL")]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        return dict(
            pushed=pushed, filled=filled, closed=len(closed),
            tp1=tp1, sl=sl, timeout=timeout,
            denom=denom, wr=wr, avg_pnl=avg_pnl,
        )

    lines = ["📊 策略胜率横向对比 (All Time)", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for sid, label in STRATEGIES:
        orders = [o for o in all_orders if o.strategy_id == sid]
        r      = _row(orders)
        if r["denom"] == 0:
            lines.append(f"\n[{label}] 暂无结算数据")
            continue
        bar    = "█" * int(r["wr"] // 10) + "░" * (10 - int(r["wr"] // 10))
        lines.append(f"\n[{label}]  {bar} {r['wr']:.1f}%")
        lines.append(f"  推送:{r['pushed']}  成交:{r['filled']}  结算:{r['closed']}")
        lines.append(f"  TP:{r['tp1']}  SL:{r['sl']}  超时:{r['timeout']}  (分母={r['denom']})")
        lines.append(f"  Avg PnL: {r['avg_pnl']:+.2f}%")

    lines.append("\n【今日 Today】")
    any_today = False
    for sid, label in STRATEGIES:
        orders = [o for o in all_orders
                  if o.strategy_id == sid and (o.created_at or "").startswith(today_str)]
        r = _row(orders)
        if r["denom"] == 0:
            continue
        any_today = True
        lines.append(f"  [{label}] TP:{r['tp1']} SL:{r['sl']} 胜率:{r['wr']:.0f}%")
    if not any_today:
        lines.append("  今日暂无结算")

    lines.append("\n发送 /conditions <策略> 查看完整过滤条件")
    return "\n".join(lines)


def _cmd_data() -> str:
    port = os.environ.get("DATA_API_PORT", "8765")
    auth = "需要 key 参数" if os.environ.get("DATA_API_KEY") else "⚠️ 未设置 DATA_API_KEY（无鉴权）"
    enabled = bool(os.environ.get("DATA_API_PORT") or os.environ.get("DATA_API_KEY"))
    status = "✅ 已启动" if enabled else "⛔ 未启用（设置 DATA_API_PORT 或 DATA_API_KEY 环境变量以启用）"

    return (
        f"🔌 生产只读数据 API\n"
        f"━━━━━━━━━━━━━━\n"
        f"状态: {status}\n"
        f"端口: {port}  鉴权: {auth}\n"
        f"\n接口列表:\n"
        f"  GET /api/health\n"
        f"  GET /api/orders?strategy=hotlist_v663&days=30&key=<KEY>\n"
        f"  GET /api/stats?strategy=hotlist_v663&key=<KEY>\n"
        f"  GET /api/signals?strategy=hotlist_v663&hours=48&key=<KEY>\n"
        f"\n示例（在开发端调用）:\n"
        f"  import requests\n"
        f"  r = requests.get('https://<prod-domain>:{port}/api/orders',\n"
        f"      params={{'strategy':'hotlist_v663','days':7,'key':'<KEY>'}})\n"
        f"  orders = r.json()['orders']"
    )


def _cmd_v66() -> str:
    from binance_ai_trader.hotlist.pg_watchlist_repo import V66WatchlistPgRepository
    repo = V66WatchlistPgRepository()
    active = repo.active()

    lines = ["📡 V66 监控池状态", "━━━━━━━━━━━━━━"]
    if not active:
        lines.append("监控池为空（等待下次扫描填充）")
        return "\n".join(lines)

    lines.append(f"活跃币: {len(active)} 个")
    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    for item in active:
        try:
            exp = datetime.fromisoformat(item.expires_at.replace("Z", "+00:00"))
            remaining_min = int((exp - datetime.now(UTC)).total_seconds() // 60)
            remaining = f"{remaining_min}min" if remaining_min > 0 else "即将过期"
        except Exception:
            remaining = "—"
        lines.append(
            f"  {item.source:<6} rank:{item.last_rank}  {item.symbol:<14} "
            f"观测:{item.observation_count}次  剩余:{remaining}"
        )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Command server
# ─────────────────────────────────────────────────────────────────────────────

class TelegramCommandServer:
    """Polls Telegram for /commands and dispatches to handler functions.

    Runs as a daemon thread — never crashes run_server.py.
    Only responds to user IDs in TELEGRAM_ADMIN_USER_ID env var.
    """

    def __init__(
        self,
        notifier,
        db_path: Path,
        universe_config,
        admin_user_ids: set[int],
    ) -> None:
        self._notifier      = notifier
        self._db_path       = db_path
        self._universe_cfg  = universe_config
        self._admin_ids     = admin_user_ids
        self._offset        = 0
        self._running       = False

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Launch polling as a background daemon thread."""
        t = threading.Thread(target=self._safe_loop, name="tg-cmd-server", daemon=True)
        t.start()
        log.info("[CmdServer] started — admin_ids=%s", self._admin_ids)

    # ------------------------------------------------------------------
    def _safe_loop(self) -> None:
        """Outer loop: catches all exceptions so the thread never dies silently."""
        while True:
            try:
                self._poll_loop()
            except Exception:
                log.exception("[CmdServer] poll loop crashed, restarting in 30s")
                time.sleep(30)

    def _poll_loop(self) -> None:
        self._running = True
        while True:
            try:
                updates = self._notifier.get_updates(offset=self._offset, timeout=5)
            except Exception:
                log.debug("[CmdServer] getUpdates failed, will retry")
                time.sleep(_POLL_INTERVAL)
                continue

            for update in updates:
                self._offset = update.get("update_id", self._offset) + 1
                try:
                    self._handle_update(update)
                except Exception:
                    log.exception("[CmdServer] error handling update %s", update.get("update_id"))

            time.sleep(_POLL_INTERVAL)

    def _handle_update(self, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        chat_id  = msg.get("chat", {}).get("id")
        user_id  = msg.get("from", {}).get("id")
        raw_text = (msg.get("text") or "").strip()
        text     = raw_text.lower()

        if not chat_id or not text.startswith("/"):
            return

        # Strip bot username suffix (e.g. /status@MyBot → /status)
        tokens = text.split()
        cmd = tokens[0].split("@")[0]
        args = tokens[1:]
        if cmd not in _COMMANDS:
            return

        # Permission check
        if user_id not in self._admin_ids:
            log.warning("[CmdServer] denied %s from user_id=%s", cmd, user_id)
            return

        log.info("[CmdServer] %s %s from user_id=%s", cmd, args, user_id)
        reply = self._dispatch(cmd, args, user_id)
        try:
            self._notifier.reply(chat_id, reply)
        except Exception:
            log.exception("[CmdServer] failed to send reply for %s", cmd)

    def _dispatch(self, cmd: str, args: list[str], user_id: int | None = None) -> str:
        try:
            if cmd == "/help":
                return _cmd_help()
            if cmd == "/status":
                return _cmd_status(self._db_path)
            if cmd == "/market":
                return _cmd_market(self._universe_cfg)
            if cmd == "/debug":
                return _cmd_debug(self._universe_cfg)
            if cmd == "/v4debug":
                return _cmd_v4debug()
            if cmd == "/signals":
                return _cmd_signals()
            if cmd == "/orders":
                return _cmd_orders()
            if cmd == "/perf":
                return _cmd_perf()
            if cmd == "/v66":
                return _cmd_v66()
            if cmd == "/portfolio":
                return _cmd_portfolio(args, self._notifier, self._db_path)
            if cmd == "/limits":
                return _cmd_limits()
            if cmd == "/setlimit":
                return _cmd_setlimit(args, user_id)
            if cmd == "/livestatus":
                return _cmd_livestatus()
            if cmd == "/livemode":
                return _cmd_livemode(args, user_id)
            if cmd == "/setlive":
                return _cmd_setlive(args, user_id)
            if cmd == "/paperstatus":
                return _cmd_paperstatus()
            if cmd == "/paperon":
                return _cmd_paperon(args, user_id)
            if cmd == "/paperoff":
                return _cmd_paperoff(args, user_id)
            if cmd == "/winrates":
                return _cmd_winrates()
            if cmd == "/conditions":
                return _cmd_conditions(args)
            if cmd == "/data":
                return _cmd_data()
            return "❓ 未知指令，发送 /help 查看列表"
        except Exception as exc:
            log.exception("[CmdServer] command %s failed", cmd)
            return f"❌ {cmd} 执行出错: {type(exc).__name__}: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# Factory helper — called from run_server.py
# ─────────────────────────────────────────────────────────────────────────────

def start_command_server(
    notifier,
    db_path: Path,
    universe_config,
) -> TelegramCommandServer | None:
    """Read TELEGRAM_ADMIN_USER_ID from env and start command server.

    Returns None (silently) if notifier is None or no admin IDs configured.
    Safe to call unconditionally — never raises.

    Also starts the read-only HTTP data API if DATA_API_PORT or DATA_API_KEY
    env vars are set (independent of Telegram — starts even if notifier is None).
    """
    # Always attempt to start data API (independent of Telegram)
    try:
        from binance_ai_trader.v3.telegram.data_api import start_data_api
        start_data_api()
    except Exception:
        log.exception("[CmdServer] failed to start data API — continuing")

    if notifier is None:
        return None
    try:
        raw = os.environ.get("TELEGRAM_ADMIN_USER_ID", "").strip()
        if not raw:
            log.info("[CmdServer] TELEGRAM_ADMIN_USER_ID not set — command server disabled")
            return None
        admin_ids = {int(uid.strip()) for uid in raw.split(",") if uid.strip()}
        if not admin_ids:
            log.warning("[CmdServer] no valid admin IDs parsed — command server disabled")
            return None
        server = TelegramCommandServer(notifier, db_path, universe_config, admin_ids)
        server.start()
        return server
    except Exception:
        log.exception("[CmdServer] failed to start — continuing without command server")
        return None
