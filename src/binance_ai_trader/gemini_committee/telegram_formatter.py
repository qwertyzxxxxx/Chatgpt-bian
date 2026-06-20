from __future__ import annotations

from .models import CommitteeDecision, SkipResult

_MAX_CHUNK = 4096


def _chunks(text: str) -> list[str]:
    parts = []
    while text:
        parts.append(text[:_MAX_CHUNK])
        text = text[_MAX_CHUNK:]
    return parts


def format_trade(decision: CommitteeDecision) -> list[str]:
    if decision.decision == "NO_TRADE":
        body = (
            "🤖 Gemini 终极建议\n"
            "策略来源：🤖 Gemini AI 委员会（Hotlist 动量候选池）\n\n"
            "结论：NO_TRADE\n"
            "最佳币：NONE\n"
            f"数据质量：{decision.data_quality}\n\n"
            "AI理由：\n"
        )
        for r in decision.reasons:
            body += f"• {r}\n"
        body += "\n仅供研究 | 不进行实盘交易"
        return _chunks(body)

    body = (
        "🏆 Gemini 终极建议\n"
        "策略来源：🤖 Gemini AI 委员会（Hotlist 动量候选池）\n\n"
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
            body += f"• {rj.get('symbol', '?')} — {rj.get('reason', '?')}\n"

    body += f"\n数据质量：{decision.data_quality}\n"
    body += "\n仅供研究 | 不进行实盘交易"
    return _chunks(body)


def format_skipped(result: SkipResult) -> list[str]:
    body = f"🤖 Gemini 委员会 已跳过\n原因: {result.reason}"
    return _chunks(body)
