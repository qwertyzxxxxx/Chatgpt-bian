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
    "/winrates", "/conditions", "/data", "/review",
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
        "/conditions [v66|v663|v3|wave_long|wave_short|c1|c2|c3] 📋 策略過濾條件詳情\n"
        "/review <strategy> <days> 📊 策略複盤（健康/方向/因子對比/虧損原因/漏斗）\n"
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


def _cmd_conditions(args: list[str]) -> str:
    """Read filter conditions + all-time performance stats for every strategy."""
    import importlib
    import os
    import subprocess

    # ── 策略別名表：alias → (module_path, display_label, style) ──────────────
    ALIASES: dict[str, tuple[str, str, str]] = {
        "v66":       ("binance_ai_trader.v3.strategies.v66",           "V66",         "watchlist"),
        "v662":      ("binance_ai_trader.v3.strategies.v662",          "V662",        "watchlist"),
        "v663":      ("binance_ai_trader.v3.strategies.v663",          "V663",        "watchlist"),
        "v664":      ("binance_ai_trader.v3.strategies.v664",          "V664",        "watchlist"),
        "v3":        ("binance_ai_trader.v3.strategies.hotlist",       "V3/momentum", "extended"),
        "wave_long": ("binance_ai_trader.v3.strategies.wave_long",     "wave_long",   "extended"),
        "wave_short":("binance_ai_trader.v3.strategies.wave_short",    "wave_short",  "extended"),
        "c1":        ("binance_ai_trader.classic.strategies.c1",       "classic_c1",  "extended"),
        "c2":        ("binance_ai_trader.classic.strategies.c2",       "classic_c2",  "extended"),
        "c3":        ("binance_ai_trader.classic.strategies.c3",       "classic_c3",  "extended"),
    }
    _NORMALIZE = {
        "momentum_v3": "v3", "hotlist_momentum_v3": "v3",
        "hotlist_v66": "v66", "hotlist_v662": "v662",
        "hotlist_v663": "v663", "hotlist_v664": "v664",
        "classic_c1": "c1", "classic_c2": "c2", "classic_c3": "c3",
        "wavelong": "wave_long", "waveshort": "wave_short",
    }

    raw = (args[0].lower() if args else "").strip()
    key = _NORMALIZE.get(raw, raw)
    if key and key not in ALIASES:
        all_keys = " / ".join(ALIASES)
        return f"❓ 未知策略 '{raw}'，可選:\n{all_keys}\n\n用法: /conditions  （全部）  /conditions v66  /conditions c1"
    targets = {key: ALIASES[key]} if key else ALIASES

    # ── Git 版本 ──────────────────────────────────────────────────────────────
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
        ).stdout.strip() or "unknown"
        commit_date = subprocess.run(
            ["git", "log", "-1", "--format=%ci"], capture_output=True, text=True,
        ).stdout.strip()[:16] or "unknown"
    except Exception:
        commit, commit_date = "unknown", "unknown"

    # ── 讀取全部訂單（用於績效統計）─────────────────────────────────────────
    _all_orders: list = []
    try:
        from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
        _all_orders = V3PaperOrderRepository().load_all()
    except Exception:
        pass

    def _compute_stats(strategy_id: str) -> dict:
        orders = [o for o in _all_orders if o.strategy_id == strategy_id]
        signals       = len(orders)
        trades        = sum(1 for o in orders if o.filled_at is not None)
        open_tracking = sum(1 for o in orders if o.status in ("OPEN", "FILLED"))
        closed        = [o for o in orders if o.status == "CLOSED"]
        tp_orders     = [o for o in closed if o.result in ("TP1", "TP2")]
        tp1_orders    = [o for o in closed if o.result == "TP1"]
        tp2_orders    = [o for o in closed if o.result == "TP2"]
        sl_orders     = [o for o in closed if o.result == "SL"]
        timeout_orders = [o for o in orders if o.result == "TIMEOUT"]
        timeout_settled = [o for o in timeout_orders if o.pnl_pct is not None]
        tp_count, sl_count = len(tp_orders), len(sl_orders)
        tp_sl_resolved = tp_count + sl_count
        tp_sl_rate = tp_count / tp_sl_resolved * 100 if tp_sl_resolved else None
        pnl_closed = [o for o in closed if o.pnl_pct is not None]
        wins_pnl   = [float(o.pnl_pct) for o in pnl_closed if o.pnl_pct > 0]
        loss_pnl   = [float(o.pnl_pct) for o in pnl_closed if o.pnl_pct < 0]
        total_net  = sum(float(o.pnl_pct) for o in pnl_closed)
        net_wr     = len(wins_pnl) / len(pnl_closed) * 100 if pnl_closed else None
        avg_win    = sum(wins_pnl) / len(wins_pnl)   if wins_pnl else None
        avg_loss   = sum(loss_pnl) / len(loss_pnl)   if loss_pnl else None
        profit_factor = (sum(wins_pnl) / abs(sum(loss_pnl))
                         if wins_pnl and loss_pnl else None)
        max_loss   = min(loss_pnl) if loss_pnl else None
        # per-direction breakdown
        def _dir_stats(direction: str):
            dc = [o for o in closed if getattr(o, "direction", None) == direction]
            dp = [o for o in dc if o.pnl_pct is not None]
            dw = [float(o.pnl_pct) for o in dp if o.pnl_pct > 0]
            return len(dc), (len(dw)/len(dp)*100 if dp else None), (sum(float(o.pnl_pct) for o in dp) if dp else None)
        long_n, long_wr, long_pnl   = _dir_stats("LONG")
        short_n, short_wr, short_pnl = _dir_stats("SHORT")
        return dict(
            signals=signals, trades=trades, open_tracking=open_tracking,
            closed=len(closed),
            tp=tp_count, tp1=len(tp1_orders), tp2=len(tp2_orders),
            sl=sl_count, tp_sl_resolved=tp_sl_resolved,
            timeout=len(timeout_orders), timeout_settled=len(timeout_settled),
            tp_sl_rate=tp_sl_rate, net_wr=net_wr,
            total_net=total_net,
            avg_win=avg_win, avg_loss=avg_loss,
            profit_factor=profit_factor, max_loss=max_loss,
            long_n=long_n, long_wr=long_wr, long_pnl=long_pnl,
            short_n=short_n, short_wr=short_wr, short_pnl=short_pnl,
        )

    def _pct(v, sign=True, dec=1):
        if v is None: return "—"
        return f"{v:+.{dec}f}%" if sign else f"{v:.{dec}f}%"

    def _append_stats(lines: list, strategy_id: str) -> None:
        """Append performance stats block for a strategy."""
        s = _compute_stats(strategy_id)
        lines.append(f"  ── 📊 績效統計（全時段） ──────────────────────")
        if s["signals"] == 0:
            lines.append(f"  暫無訂單記錄")
            return
        lines.append(
            f"  訂單:  信號{s['signals']}  成交{s['trades']}  持倉中{s['open_tracking']}  已結算{s['closed']}"
        )
        lines.append(
            f"  結果:  TP1={s['tp1']} TP2={s['tp2']} SL={s['sl']}"
            + (f" TIMEOUT={s['timeout']}(已市价={s['timeout_settled']})" if s["timeout"] else "")
        )
        # hit rate bar
        if s["tp_sl_rate"] is not None:
            filled = int(s["tp_sl_rate"] // 10)
            bar = "█" * filled + "░" * (10 - filled)
            lines.append(f"  TP/(TP+SL): {bar} {s['tp_sl_rate']:.1f}%  （共{s['tp_sl_resolved']}筆已判定）")
        else:
            lines.append(f"  TP/(TP+SL): — （無已判定訂單）")
        lines.append(f"  淨勝率:    {_pct(s['net_wr'], sign=False)}  （含TIMEOUT已結算）")
        lines.append(f"  累計收益:  {_pct(s['total_net'])}  avg_win={_pct(s['avg_win'])}  avg_loss={_pct(s['avg_loss'])}")
        pf_str = f"{s['profit_factor']:.2f}" if s['profit_factor'] else "—"
        lines.append(f"  盈虧比:    {pf_str}  最大單筆虧損: {_pct(s['max_loss'])}")
        # per-direction
        if s["long_n"] > 0 or s["short_n"] > 0:
            l_str = f"LONG  {s['long_n']}筆  勝率{_pct(s['long_wr'],False)}  淨{_pct(s['long_pnl'])}" if s["long_n"] else ""
            s_str = f"SHORT {s['short_n']}筆  勝率{_pct(s['short_wr'],False)}  淨{_pct(s['short_pnl'])}" if s["short_n"] else ""
            for ds in filter(None, [l_str, s_str]):
                lines.append(f"    {ds}")

    # ── 啟用狀態 ──────────────────────────────────────────────────────────────
    _ENV_STATUS: dict[str, str] = {
        "always_on（v3 主策略，默認啟動）": "✅ 啟用（默認）",
        "ENABLE_WAVE_LONG=true":  "✅ 啟用" if os.environ.get("ENABLE_WAVE_LONG","").lower()=="true" else "⏸ 停用（ENABLE_WAVE_LONG 未設）",
        "ENABLE_WAVE_SHORT=true": "✅ 啟用" if os.environ.get("ENABLE_WAVE_SHORT","").lower()=="true" else "⏸ 停用（ENABLE_WAVE_SHORT 未設）",
        "ENABLE_CLASSIC=true":    "✅ 啟用" if os.environ.get("ENABLE_CLASSIC","").lower()=="true" else "⏸ 停用（ENABLE_CLASSIC 未設）",
    }
    _TREND_DESC = {
        None:            "未使用",
        "trend_aligned": "價格在EMA20/50正確一側",
        "triple_ema":    "EMA10>EMA20>EMA50 三線排列",
    }

    lines = [
        "📋 策略條件 + 績效（讀取自策略模組 CONDITIONS 常量 + PaperOrder DB）",
        "━━━━━━━━━━━━━━",
        f"代碼版本: {commit}  {commit_date}",
        f"訂單庫合計: {len(_all_orders)} 筆",
    ]

    for alias, (mod_path, label, style) in targets.items():
        try:
            mod = importlib.import_module(mod_path)
            c   = mod.CONDITIONS
        except Exception as exc:
            lines.append(f"\n[{label}] ❌ 加載失敗: {exc}")
            continue

        sid = c["strategy_id"]
        lines.append(f"\n{'━'*22}")
        lines.append(f"【{label}】  strategy_id={sid}  version={c['strategy_version']}")

        if style == "watchlist":
            mm = c["min_move_pct"]
            lines.append(f"  方向:           {c['direction']}")
            lines.append(f"  使用周期:       15m（入場）/ 1h（趨勢）" + ("/ 4h（趨勢）" if c["trend_4h"] else ""))
            lines.append(f"  候選池:         Top{c['gainers']}漲+Top{c['losers']}跌  每輪最多{c['max_opp']}個信號")
            lines.append(f"  成交額門檻:     ≥{float(c['min_quote_volume']):,.0f} USDT")
            lines.append(f"  漲跌幅門檻:     {'|24h|≥'+str(mm)+'%' if mm and mm>0 else '無（≥0%）'}")
            lines.append(f"  D1條件:         未使用")
            lines.append(f"  H4條件:         {_TREND_DESC.get(c['trend_4h'], str(c['trend_4h']))}")
            lines.append(f"  H1條件:         {_TREND_DESC.get(c['trend_1h'], str(c['trend_1h']))}")
            lines.append(f"  M15條件:        EMA20 回踩入場")
            vr = c.get("min_vol_ratio")
            if c.get("min_vol_ratio_long") or c.get("max_vol_ratio_short"):
                long_th = c.get("min_vol_ratio_long") or c.get("min_vol_ratio") or "—"
                short_cap = c.get("max_vol_ratio_short")
                if c.get("min_vol_ratio_long"):
                    # v663 style: LONG放量, SHORT縮量
                    vol_str = f"LONG量比≥{long_th}x；SHORT量比<{short_cap}x（縮量做空）"
                else:
                    # v66 style: 統一下限 + SHORT上限
                    vol_str = f"量比≥{long_th}x；SHORT額外限制≤{short_cap}x（超量不做空）"
            elif vr:
                vol_str = f"量比≥{vr}x（放量確認）"
            elif c.get("require_low_vol"):
                vol_str = "量比<1.0（縮量確認）"
            else:
                vol_str = "無要求"
            lines.append(f"  量能條件:       {vol_str}")
            lines.append(f"  EMA條件:        15m EMA20（入場基準）；1h/4h EMA（趨勢判斷）")
            lines.append(f"  ATR條件:        ATR14 → 入場price buffer(×0.25) + 止損下限")
            lines.append(f"  結構條件:       Swing High/Low（前20根）→ 止損基準")
            ed = c.get("max_entry_dist")
            lines.append(f"  入場觸發:       {'EMA20±'+str(ed)+'% 已到位直接觸發' if ed else 'EMA20±0.25ATR 限價掛單等待回踩'}")
            lines.append(f"  止損計算:       LONG: min(swing_low_20,entry−ATR14)  SHORT: max(swing_high_20,entry+ATR14)")
            lines.append(f"  止盈計算:       TP1: entry±risk×1  TP2: entry±risk×2")
            lines.append(f"  RR:             ≥{c['min_rr']}")
            lines.append(f"  止損上限:       ≤{c['max_stop_pct']}%（{'止損下限≥'+str(c['min_stop_pct'])+'%' if c.get('min_stop_pct') else '無下限'}）")
            lines.append(f"  TIMEOUT:        {c['expiry_min']}min  TTL監控:{c['max_ttl_min']}min  刷新:{c['refresh_min']}min")
            lines.append(f"  冷卻/去重:      dedup_hours 同幣同方向（/setlimit 可調）  每輪≤{c['max_opp']}個")
            v66_live = os.environ.get("LIVE_TRADING_ENABLED","").lower()=="true"
            lines.append(f"  當前啟用:       ✅ 紙盤運行中" + ("  ✅ 實盤鏡像啟用" if v66_live else "  ⏸ 實盤未啟用"))

        else:
            def _f(k: str, _c: dict = c) -> str:  # noqa: E731
                v = _c.get(k, "未使用")
                return str(v) if v is not None else "未使用"

            lines.append(f"  方向:           {_f('direction')}")
            lines.append(f"  使用周期:       {_f('timeframes')}")
            lines.append(f"  候選池條件:     {_f('pool')}")
            vol_raw = c.get("min_quote_volume")
            lines.append(f"  成交額門檻:     {('≥'+f'{float(vol_raw):,.0f} USDT') if isinstance(vol_raw,(int,float)) or hasattr(vol_raw,'__float__') else _f('min_quote_volume')}")
            lines.append(f"  漲跌幅門檻:     {_f('min_move_pct')}")
            lines.append(f"  D1條件:         {_f('d1')}")
            lines.append(f"  H4條件:         {_f('h4')}")
            lines.append(f"  H1條件:         {_f('h1')}")
            lines.append(f"  M15條件:        {_f('m15')}")
            lines.append(f"  EMA條件:        {_f('ema')}")
            lines.append(f"  RSI條件:        {_f('rsi')}")
            lines.append(f"  ATR條件:        {_f('atr')}")
            lines.append(f"  量能條件:       {_f('volume')}")
            lines.append(f"  結構條件:       {_f('structure')}")
            lines.append(f"  入場觸發:       {_f('entry_trigger')}")
            lines.append(f"  止損計算:       {_f('sl_calc')}")
            lines.append(f"  止盈計算:       {_f('tp_calc')}")
            lines.append(f"  RR:             {_f('rr')}")
            th = c.get("timeout_hours")
            lines.append(f"  TIMEOUT:        {str(th)+'h ('+str(int(th)*60)+'min)' if isinstance(th,(int,float)) else _f('timeout_hours')}")
            wh = c.get("watch_hours")
            if wh:
                lines.append(f"  觀察窗口:       {wh}h（突破後等待回踩）")
            ch = c.get("cooldown_hours")
            lines.append(f"  冷卻時間:       {str(ch)+'h' if isinstance(ch,(int,float)) else _f('cooldown_hours')}")
            lines.append(f"  同幣去重:       {_f('dedup')}")
            lines.append(f"  每輪最大信號:   {_f('max_signals')}")
            if c.get("score_threshold"):
                lines.append(f"  評分門檻:       {_f('score_threshold')}")
            if c.get("max_stop_pct"):
                lines.append(f"  止損上限:       ≤{_f('max_stop_pct')}%")
            if c.get("live_max_stop_pct"):
                lines.append(f"  實盤止損上限:   LONG≤{_f('live_max_stop_pct')}%  SHORT≤{_f('live_short_max_stop_pct')}%")
            env_key = _f("enabled_env")
            lines.append(f"  當前啟用:       {_ENV_STATUS.get(env_key, '⏸ 停用（'+env_key+'）')}")

        # ── 績效統計（每個策略都附上）────────────────────────────────────────
        _append_stats(lines, sid)

    lines.append("")
    lines.append("用法: /conditions  （全部策略）  /conditions v66  /conditions c1  /conditions wave_long")
    return "\n".join(lines)


def _cmd_winrates() -> str:
    from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
    from decimal import Decimal
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

    def _compute(orders):
        # signals: all records for this strategy
        signals       = len(orders)
        # trades: actually filled (entered the market)
        trades        = sum(1 for o in orders if o.filled_at is not None)
        # open_tracking: currently live
        open_tracking = sum(1 for o in orders if o.status in ("OPEN", "FILLED"))
        # closed_trades: all CLOSED — includes TP, SL, TIMEOUT (market-settled), EXPIRED
        closed        = [o for o in orders if o.status == "CLOSED"]
        closed_trades = len(closed)

        tp_orders = [o for o in closed if o.result in ("TP1", "TP2")]
        sl_orders = [o for o in closed if o.result == "SL"]
        tp_count  = len(tp_orders)
        sl_count  = len(sl_orders)
        # TP/SL已判定: only TP+SL (TIMEOUT excluded from denominator)
        tp_sl_resolved = tp_count + sl_count

        # timeout: all timeout results; settled = has pnl_pct (market price closed)
        timeout_orders   = [o for o in orders if o.result == "TIMEOUT"]
        timeout_settled  = [o for o in timeout_orders if o.pnl_pct is not None]
        timeout_count    = len(timeout_orders)

        # tp_sl_hit_rate = TP / (TP + SL), TIMEOUT excluded
        tp_sl_hit_rate = tp_count / tp_sl_resolved * 100 if tp_sl_resolved else None

        # net_win_rate = closed with pnl_pct > 0 / all closed_trades
        # TIMEOUT with pnl_pct counts (market-settled = real P&L realized)
        winning = [o for o in closed if o.pnl_pct is not None and o.pnl_pct > 0]
        net_win_rate = len(winning) / closed_trades * 100 if closed_trades else None

        # PnL stats over all closed orders that have a realized pnl_pct
        pnl_closed = [o for o in closed if o.pnl_pct is not None]
        wins_pnl   = [float(o.pnl_pct) for o in pnl_closed if o.pnl_pct > 0]
        loss_pnl   = [float(o.pnl_pct) for o in pnl_closed if o.pnl_pct < 0]
        total_net_pnl = sum(float(o.pnl_pct) for o in pnl_closed)
        avg_win       = sum(wins_pnl) / len(wins_pnl)   if wins_pnl else None
        avg_loss      = sum(loss_pnl) / len(loss_pnl)   if loss_pnl else None
        profit_factor = (sum(wins_pnl) / abs(sum(loss_pnl))
                         if wins_pnl and loss_pnl else None)
        max_loss      = min(loss_pnl) if loss_pnl else None

        return dict(
            signals=signals, trades=trades, open_tracking=open_tracking,
            closed_trades=closed_trades, tp=tp_count, sl=sl_count,
            tp_sl_resolved=tp_sl_resolved, timeout_count=timeout_count,
            timeout_settled=len(timeout_settled),
            tp_sl_hit_rate=tp_sl_hit_rate, net_win_rate=net_win_rate,
            total_net_pnl=total_net_pnl,
            avg_win=avg_win, avg_loss=avg_loss,
            profit_factor=profit_factor, max_loss=max_loss,
        )

    def _p(v, dec=1):
        return f"{v:+.{dec}f}%" if v is not None else "—"
    def _r(v, dec=1):
        return f"{v:.{dec}f}%" if v is not None else "—"
    def _pf(v):
        return f"{v:.2f}" if v is not None else "—"

    lines = ["📊 策略胜率横向对比 (All Time)", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]

    for sid, label in STRATEGIES:
        orders = [o for o in all_orders if o.strategy_id == sid]
        r = _compute(orders)
        lines.append(f"\n【{label}】{sid}")
        lines.append(f"  signals:        {r['signals']}")
        lines.append(f"  trades:         {r['trades']}  (已成交入场)")
        lines.append(f"  open_tracking:  {r['open_tracking']}  (当前OPEN/FILLED)")
        lines.append(f"  closed_trades:  {r['closed_trades']}")
        lines.append(f"  TP/SL已判定:   {r['tp_sl_resolved']}  (TP={r['tp']} SL={r['sl']})")
        lines.append(
            f"  timeout_count:  {r['timeout_count']}"
            + (f"  (已市价结算={r['timeout_settled']})" if r['timeout_count'] else "")
        )
        lines.append("  ─────────────────────────────")
        if r["tp_sl_hit_rate"] is not None:
            bar = "█" * int(r["tp_sl_hit_rate"] // 10) + "░" * (10 - int(r["tp_sl_hit_rate"] // 10))
            lines.append(f"  tp_sl_hit_rate: {bar} {_r(r['tp_sl_hit_rate'])}")
        else:
            lines.append("  tp_sl_hit_rate: — (暂无TP/SL结算)")
        lines.append(f"  net_win_rate:   {_r(r['net_win_rate'])}  (含TIMEOUT已结算)")
        lines.append("  ─────────────────────────────")
        lines.append(f"  total_net_pnl:  {_p(r['total_net_pnl'])}")
        lines.append(f"  avg_win:        {_p(r['avg_win'])}")
        lines.append(f"  avg_loss:       {_p(r['avg_loss'])}")
        lines.append(f"  profit_factor:  {_pf(r['profit_factor'])}")
        lines.append(f"  max_loss:       {_p(r['max_loss'])}")

    lines.append("\n【今日 Today】")
    any_today = False
    for sid, label in STRATEGIES:
        o2 = [o for o in all_orders
              if o.strategy_id == sid and (o.created_at or "").startswith(today_str)]
        r = _compute(o2)
        if r["closed_trades"] == 0 and r["open_tracking"] == 0:
            continue
        any_today = True
        lines.append(
            f"  [{label}] "
            f"TP:{r['tp']} SL:{r['sl']} TOUT:{r['timeout_count']}  "
            f"hit_rate:{_r(r['tp_sl_hit_rate'])}  "
            f"net_wr:{_r(r['net_win_rate'])}"
        )
    if not any_today:
        lines.append("  今日暂无活动")

    lines.append("\n发送 /conditions <策略> 查看过滤条件")
    return "\n".join(lines)


def _cmd_review(args: list[str]) -> str:
    """
    /review <strategy> [days]

    strategy aliases (case-insensitive):
      v66, hotlist, hotlist_v66, hotlist_momentum_v3
      v662, v663, v664
      wave_long, wave_short
      c1, classic_c1 / c2, classic_c2 / c3, classic_c3
      rsd_long, rsd_short

    days: 整數天數，0 或省略 = 全周期
    """
    _STRATEGY_ALIASES: dict[str, str] = {
        "v66":                "hotlist_momentum_v3",
        "hotlist":            "hotlist_momentum_v3",
        "hotlist_v66":        "hotlist_momentum_v3",
        "hotlist_momentum_v3":"hotlist_momentum_v3",
        "v662":               "hotlist_momentum_v662",
        "hotlist_v662":       "hotlist_momentum_v662",
        "hotlist_momentum_v662":"hotlist_momentum_v662",
        "v663":               "hotlist_momentum_v663",
        "hotlist_v663":       "hotlist_momentum_v663",
        "hotlist_momentum_v663":"hotlist_momentum_v663",
        "v664":               "hotlist_momentum_v664",
        "hotlist_v664":       "hotlist_momentum_v664",
        "hotlist_momentum_v664":"hotlist_momentum_v664",
        "wave_long":          "wave_long",
        "wavelong":           "wave_long",
        "wave_short":         "wave_short",
        "waveshort":          "wave_short",
        "c1":                 "classic_c1",
        "classic_c1":         "classic_c1",
        "c2":                 "classic_c2",
        "classic_c2":         "classic_c2",
        "c3":                 "classic_c3",
        "classic_c3":         "classic_c3",
        "rsd_long":           "rsd_long",
        "rsd_short":          "rsd_short",
    }

    _USAGE = (
        "用法: /review <策略> [天數]\n"
        "  策略: v66 / v662 / v663 / v664 / wave_long / wave_short / c1 / c2 / c3 / rsd_long / rsd_short\n"
        "  天數: 正整數，省略或 0 = 全周期\n"
        "例子: /review v66 30    /review v662 90    /review wave_long"
    )

    if not args:
        return _USAGE

    raw_strat = args[0].lower()
    strategy_id = _STRATEGY_ALIASES.get(raw_strat)
    if strategy_id is None:
        keys = sorted(_STRATEGY_ALIASES.keys())
        return (
            f"❓ 未知策略代號: {args[0]!r}\n"
            f"支援: {', '.join(keys)}\n\n{_USAGE}"
        )

    days = 0
    if len(args) >= 2:
        try:
            days = int(args[1])
            if days < 0:
                return "❌ 天數必須 ≥ 0（0 = 全周期）"
        except ValueError:
            return f"❌ 天數須為整數，收到: {args[1]!r}\n\n{_USAGE}"

    try:
        from binance_ai_trader.v3.telegram.review import format_review
        return format_review(strategy_id, days)
    except Exception as exc:
        log.exception("[CmdServer] /review failed strategy=%s days=%s", strategy_id, days)
        return f"❌ /review 執行出錯: {type(exc).__name__}: {exc}"


def _cmd_data() -> str:
    enabled = bool(os.environ.get("DATA_API_PORT") or os.environ.get("DATA_API_KEY"))
    status  = "✅ 已启动" if enabled else "⛔ 未启用（需设置 DATA_API_PORT 或 DATA_API_KEY）"
    has_key = bool(os.environ.get("DATA_API_KEY"))
    auth    = "✅ 需要 X-API-Key 请求头（或 ?key= 参数）" if has_key else "⚠️ 未设置 DATA_API_KEY（无鉴权，仅限内网）"
    host    = os.environ.get("DATA_API_HOST", "0.0.0.0")
    port    = os.environ.get("DATA_API_PORT", "8765")

    return (
        f"🔌 生产只读数据 API\n"
        f"━━━━━━━━━━━━━━\n"
        f"状态: {status}\n"
        f"监听: {host}:{port}  |  鉴权: {auth}\n"
        f"\n接口列表:\n"
        f"  GET /api/health          — 无需鉴权，返回状态+DB连通性\n"
        f"  GET /api/orders?strategy=hotlist_v663&days=30\n"
        f"  GET /api/stats?strategy=hotlist_v663\n"
        f"  GET /api/signals?strategy=hotlist_v663&hours=48\n"
        f"\n鉴权方式（任选一）:\n"
        f"  请求头: X-API-Key: <KEY>  ← 推荐\n"
        f"  参数:   ?key=<KEY>         ← 日志中key会被遮蔽\n"
        f"\n示例（externalPort=80，无需写端口号）:\n"
        f"  curl https://<prod-domain>/api/health\n"
        f"  curl -H 'X-API-Key: <KEY>' \\\n"
        f"    'https://<prod-domain>/api/orders?strategy=hotlist_v663&days=7'\n"
        f"\nPython:\n"
        f"  import requests\n"
        f"  r = requests.get('https://<prod-domain>/api/orders',\n"
        f"      headers={{'X-API-Key':'<KEY>'}},\n"
        f"      params={{'strategy':'hotlist_v663','days':7}})\n"
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
            if cmd == "/review":
                return _cmd_review(args)
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
