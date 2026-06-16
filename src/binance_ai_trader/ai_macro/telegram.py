from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from binance_ai_trader.ai_macro.models import (
    AIMacroPerformance,
    AIMacroTrade,
    MacroAnalysis,
)

_STATE_ICON = {"BULL": "📈", "BEAR": "📉", "RANGE": "📊", "RISK_OFF": "⚠️"}
_BIAS_CN = {
    "LONG_ONLY": "只做多",
    "SHORT_ONLY": "只做空",
    "BOTH": "多空均可",
    "NO_TRADE": "不交易",
}
_STATUS_ICON = {"TP2": "🏆", "TP1": "✅", "STOP": "❌", "EXPIRED": "⏰", "OPEN": "📌"}


def format_ai_macro_scan_message(
    analysis: MacroAnalysis,
    new_trades: list[AIMacroTrade],
) -> str:
    icon = _STATE_ICON.get(analysis.market_state, "")
    bias_cn = _BIAS_CN.get(analysis.trade_bias, analysis.trade_bias)
    lines = [
        "🤖 AI Macro Report",
        "",
        f"时间（UTC）: {analysis.generated_at}",
        f"市场状态: {analysis.market_state} {icon}",
        f"风险等级: {analysis.risk_grade}",
        f"交易偏向: {bias_cn}",
        f"BTC: {float(analysis.btc_change_pct):+.2f}%  ETH: {float(analysis.eth_change_pct):+.2f}%",
    ]
    if not new_trades:
        lines += ["", "─── 无新机会 ───", "", "Research Only — 仅供研究"]
        return "\n".join(lines)

    for i, trade in enumerate(new_trades, start=1):
        lines += [
            "",
            "─" * 16,
            f"Top Opportunity #{i}",
            "",
            f"📊 {trade.symbol}",
            f"方向: {trade.direction}",
            f"分数: {trade.score}/100",
            f"买入: {trade.entry}",
            f"止损: {trade.stop_loss}",
            f"TP1: {trade.tp1}",
            f"TP2: {trade.tp2}",
            f"理由: {trade.reason[:100]}",
        ]
    lines += ["", "Research Only — 仅供研究"]
    return "\n".join(lines)


def format_ai_macro_review_message(
    open_trades: list[AIMacroTrade],
    current_prices: dict[str, Decimal],
    now_iso: str,
) -> str:
    lines = ["📋 Open Trades Review", "", f"时间（UTC）: {now_iso}"]
    if not open_trades:
        lines += ["", "─── 无持仓 ───", "", "Research Only — 仅供研究"]
        return "\n".join(lines)

    try:
        now = datetime.now(UTC).astimezone(UTC)
    except Exception:
        now = None

    for trade in open_trades:
        icon = _STATUS_ICON.get(trade.status, "📌")
        lines += ["", f"{icon} {trade.symbol} ({trade.direction})"]
        lines.append(f"状态: {trade.status}")
        lines.append(f"开仓: {trade.entry}")
        current = current_prices.get(trade.symbol)
        if current is not None:
            if trade.direction == "LONG":
                pnl = (current - trade.entry) / trade.entry * Decimal("100")
            else:
                pnl = (trade.entry - current) / trade.entry * Decimal("100")
            lines.append(f"当前价: {current}")
            lines.append(f"当前盈亏: {float(pnl):+.2f}%")
        if now is not None:
            try:
                created = datetime.fromisoformat(trade.created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                expires = created + timedelta(hours=48)
                remaining = max(Decimal("0"), Decimal(str((expires - now).total_seconds() / 3600)))
                lines.append(f"距到期: {float(remaining):.0f}小时")
            except Exception:
                pass

    lines += ["", "Research Only — 仅供研究"]
    return "\n".join(lines)


def format_ai_macro_settle_message(settled_trades: list[AIMacroTrade]) -> str:
    lines = ["⏰ Trade Settlement", ""]
    if not settled_trades:
        lines += ["无到期交易", "", "Research Only — 仅供研究"]
        return "\n".join(lines)

    for trade in settled_trades:
        icon = _STATUS_ICON.get(trade.status, "📌")
        lines += [
            f"{icon} {trade.symbol}",
            f"方向: {trade.direction}",
            f"结果: {trade.status}",
            f"最终收益: {float(trade.pnl_pct or Decimal('0')):+.2f}%",
            "",
        ]
    lines.append("Research Only — 仅供研究")
    return "\n".join(lines)


def format_ai_macro_performance_message(perf: AIMacroPerformance) -> str:
    lines = [
        "📊 AI Macro Performance",
        "",
        f"总单数: {perf.total_trades}",
        f"持仓中: {perf.open_trades}",
        f"已结算: {perf.closed_trades}",
        f"胜率: {float(perf.win_rate):.1f}%",
        f"TP1率: {float(perf.tp1_rate):.1f}%",
        f"TP2率: {float(perf.tp2_rate):.1f}%",
        f"平均收益: {float(perf.avg_pnl_pct):+.2f}%",
        f"虚拟账户: {float(perf.virtual_balance):.2f} U",
        "",
        "Research Only — 仅供研究",
    ]
    return "\n".join(lines)
