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


def _data_quality_label(q: str) -> str:
    return {"GOOD": "✅ 完整", "PARTIAL": "⚠️ 部分缺失", "POOR": "❌ 数据差"}.get(q, q)


def format_review(decision: WatchDecision, stats: dict | None = None) -> list[str]:
    if decision.decision == "NO_TRADE":
        body = (
            "🤖 排行榜 Gemini 建议\n"
            "来源：🏆 排行榜观察池（Leaderboard Watch + Gemini AI）\n\n"
            "📌 结论：暂无机会，本轮不交易\n\n"
            "AI分析：\n"
        )
        for r in decision.reasons:
            body += f"• {r}\n"

        if decision.reject_reasons:
            body += "\n── 各币分析 ──\n"
            for rj in decision.reject_reasons:
                body += f"❌ {rj.get('symbol', '?')} — {rj.get('reason', '?')}\n"

        if stats:
            ccount = stats.get("candidate_count", 0)
            missing = stats.get("missing_field_counts") or {}
            missing_count = sum(missing.values())
            body += f"\n候选数量：{ccount} 个\n"
            if missing_count:
                body += f"数据完整度：部分币种指标（如RSI/ATR/趋势）从交易所获取失败\n"

        body += "\n仅供研究 | 不进行实盘交易"
        return _chunks(body)

    body = (
        "🏆 排行榜 Gemini 建议\n"
        "来源：🏆 排行榜观察池（Leaderboard Watch + Gemini AI）\n\n"
        f"📌 结论：{decision.decision}  {decision.rating}级  {decision.risk_level}风险\n"
        f"🎯 选币：{decision.best_symbol}  {'做多 📈' if decision.direction == 'LONG' else '做空 📉' if decision.direction == 'SHORT' else decision.direction}\n\n"
        "── 挂单计划 ──\n"
        f"挂单价：{decision.entry}\n"
        f"止  损：{decision.stop_loss}\n"
        f"目标1：{decision.tp1}\n"
        f"目标2：{decision.tp2}\n"
        f"盈亏比：{decision.rr}\n\n"
        "── 选币理由 ──\n"
    )
    for r in decision.reasons:
        body += f"• {r}\n"

    if decision.reject_reasons:
        body += "\n── 其他候选（未选原因）──\n"
        for rj in decision.reject_reasons:
            body += f"❌ {rj.get('symbol', '?')} — {rj.get('reason', '?')}\n"

    dq = _data_quality_label(decision.data_quality)
    body += f"\n数据质量：{dq}"
    body += "\n\n仅供研究 | 不进行实盘交易"
    return _chunks(body)


def format_skipped(result: SkipResult) -> list[str]:
    reason_map = {
        "cooldown_active": "冷却期未到，跳过本轮",
        "existing_open_recommendation": "当前已有持仓中的推荐，等待结算",
        "no_candidates": "无符合条件的候选币种",
        "gemini_api_key_missing": "Gemini API Key 未配置",
    }
    reason_text = reason_map.get(result.reason, result.reason)
    body = f"📊 排行榜 Gemini 已跳过\n原因: {reason_text}"
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
