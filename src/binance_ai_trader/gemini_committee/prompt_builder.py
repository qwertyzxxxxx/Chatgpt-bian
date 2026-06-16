from __future__ import annotations

import json

from .models import Candidate

_ANTI_HALLUCINATION = """
你是一名量化交易分析员。

规则（强制遵守）：
1. 你只能基于下方 JSON 中已有的字段做判断。
2. 禁止编造任何以下内容：新闻、解锁计划、黑客事件、庄家行为、机构动向、利好利空消息、项目基本面、链上资金流动。
3. 如果某字段值为 "UNKNOWN"，你必须写 "UNKNOWN"，不得猜测。
4. 你只能从以下候选中选择一个，或者选择 NO_TRADE。
5. 如果所有候选都不适合交易，必须输出 should_trade = false 且 decision = "NO_TRADE"。

你必须严格输出以下 JSON 格式，不得包含任何注释或额外字段：

{
  "decision": "TRADE 或 NO_TRADE",
  "best_symbol": "交易对或NONE",
  "direction": "LONG 或 SHORT 或 UNKNOWN",
  "rating": "A+ 或 A 或 B 或 C",
  "entry": "价格字符串",
  "stop_loss": "价格字符串",
  "tp1": "价格字符串",
  "tp2": "价格字符串",
  "rr": "风险回报比字符串",
  "risk_level": "LOW 或 MEDIUM 或 HIGH",
  "should_trade": true 或 false,
  "reasons": ["理由1", "理由2", "理由3"],
  "reject_reasons": [{"symbol": "X", "reason": "原因"}],
  "data_quality": "GOOD 或 PARTIAL 或 POOR"
}
""".strip()


def build_prompt(candidates: list[Candidate]) -> str:
    candidates_json = json.dumps(
        [c.to_dict() for c in candidates],
        ensure_ascii=False,
        indent=2,
    )
    return f"{_ANTI_HALLUCINATION}\n\n候选数据：\n{candidates_json}"
