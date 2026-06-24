from __future__ import annotations

from .models import PoolStatus, PoolSummary, SkipResult, WatchDecision

_MAX_CHUNK = 4096


def _chunks(text: str) -> list[str]:
    parts = []
    while text:
        parts.append(text[:_MAX_CHUNK])
        text = text[_MAX_CHUNK:]
    return parts


def format_status(status: PoolStatus) -> list[str]:
    body = (
        "📊 排行榜观察池\n\n"
        f"NEW:     {status.new_count}\n"
        f"ACTIVE:  {status.active_count}\n"
        f"OPEN:    {status.open_count}\n"
        f"CLOSED:  {status.closed_count}\n"
        f"EXPIRED: {status.expired_count}\n"
    )
    if status.top_active:
        body += "\n── ACTIVE Top20 ──\n"
        for i, item in enumerate(status.top_active[:20], 1):
            body += (
                f"{i:2}. {item.symbol:<16} "
                f"{item.latest_rank_type:<7} "
                f"#{item.best_rank_position} "
                f"{item.latest_change_24h}%\n"
            )
    body += "\n仅供研究"
    return _chunks(body)


def _format_no_trade_diagnostics(decision: WatchDecision, stats: dict | None) -> str:
    """Build the NO_TRADE diagnostic block: candidate count, data quality, top
    reject reasons, missing-fields Top5, and risk distribution. Returns an empty
    string when no stats are supplied (preserving the legacy message shape)."""
    if not stats:
        return ""

    section = "\n── 诊断 ──\n"
    section += f"候选数量：{stats.get('candidate_count', 0)}\n"
    section += f"数据质量：{decision.data_quality}\n"

    risk = stats.get("risk_distribution") or {}
    section += (
        "风险分布："
        f"HIGH {risk.get('HIGH', 0)} / "
        f"MEDIUM {risk.get('MEDIUM', 0)} / "
        f"LOW {risk.get('LOW', 0)}\n"
    )

    # Aggregate reject reasons (per-symbol) into counts, highest first.
    reason_counts: dict[str, int] = {}
    for rj in decision.reject_reasons:
        reason = str(rj.get("reason", "?")).strip() or "?"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if reason_counts:
        section += "\n主要拒绝原因 Top:\n"
        top_reasons = sorted(reason_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
        for reason, count in top_reasons:
            section += f"• {reason} ×{count}\n"

    missing = stats.get("missing_field_counts") or {}
    if missing:
        section += "\n缺失字段 Top5:\n"
        top_missing = sorted(missing.items(), key=lambda x: (-x[1], x[0]))[:5]
        for field, count in top_missing:
            section += f"• {field} ×{count}\n"

    return section


def format_review(decision: WatchDecision, stats: dict | None = None) -> list[str]:
    if decision.decision == "NO_TRADE":
        body = (
            "🤖 排行榜 Gemini 建议\n"
            "策略来源：🏆 排行榜观察池（Leaderboard Watch + Gemini AI）\n\n"
            "结论：NO_TRADE\n"
            "最佳币：NONE\n"
            f"数据质量：{decision.data_quality}\n\n"
            "AI理由：\n"
        )
        for r in decision.reasons:
            body += f"• {r}\n"
        body += _format_no_trade_diagnostics(decision, stats)
        body += "\n仅供研究 | 不进行实盘交易"
        return _chunks(body)

    body = (
        "🏆 排行榜 Gemini 建议\n"
        "策略来源：🏆 排行榜观察池（Leaderboard Watch + Gemini AI）\n\n"
        f"结论：{decision.decision}\n"
        f"最佳币：{decision.best_symbol}\n"
        f"方向：{decision.direction}\n"
        f"评级：{decision.rating}\n\n"
        f"买入：{decision.entry}\n"
        f"止损：{decision.stop_loss}\n"
        f"TP1：{decision.tp1}\n"
        f"TP2：{decision.tp2}\n"
        f"RR：{decision.rr}\n"
        f"风险：{decision.risk_level}\n\n"
        "AI理由：\n"
    )
    for r in decision.reasons:
        body += f"• {r}\n"

    if decision.reject_reasons:
        body += "\n不选其他币原因：\n"
        for rj in decision.reject_reasons:
            body += f"• {rj.get('symbol','?')} — {rj.get('reason','?')}\n"

    body += f"\n数据质量：{decision.data_quality}\n"
    body += "\n仅供研究 | 不进行实盘交易"
    return _chunks(body)


def format_skipped(result: SkipResult) -> list[str]:
    body = f"📊 排行榜 Gemini 已跳过\n原因: {result.reason}"
    return _chunks(body)


def format_summary(summary: PoolSummary) -> list[str]:
    body = (
        "📈 排行榜观察池绩效\n\n"
        f"总审查次数: {summary.total_reviews}\n"
        f"  TRADE: {summary.trade_count}\n"
        f"  NO_TRADE: {summary.no_trade_count}\n\n"
        f"持仓中(OPEN): {summary.open_count}\n"
        f"TP1命中: {summary.tp1_count}\n"
        f"TP2命中: {summary.tp2_count}\n"
        f"止损: {summary.sl_count}\n"
        f"超时: {summary.timeout_count}\n\n"
        f"胜率: {summary.win_rate}\n\n"
        "仅供研究"
    )
    return _chunks(body)
