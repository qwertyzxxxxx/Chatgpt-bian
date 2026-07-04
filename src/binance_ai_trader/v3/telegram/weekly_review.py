"""V3 Weekly Strategy Review — auto-generated every 7 days.

Outputs: trades, win rate, PnL, RR, max streak, top/worst symbols,
LONG vs SHORT split. Never modifies parameters — analysis only.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal

from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v3.paper.repository import V3PaperOrder, V3PaperOrderRepository
from binance_ai_trader.v3.performance.calculator import V3PerformanceCalculator

log = logging.getLogger(__name__)


def _streak(orders: list[V3PaperOrder]) -> tuple[int, int]:
    """Return (max_win_streak, max_loss_streak)."""
    settled = [o for o in orders if o.result in ("TP1", "SL")]
    if not settled:
        return 0, 0
    max_win = max_loss = cur_win = cur_loss = 0
    for o in settled:
        if o.result == "TP1":
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win  = max(max_win,  cur_win)
        max_loss = max(max_loss, cur_loss)
    return max_win, max_loss


def _top_worst(orders: list[V3PaperOrder]) -> tuple[str, str]:
    pnl_by_sym: dict[str, list[Decimal]] = defaultdict(list)
    for o in orders:
        if o.pnl_pct is not None:
            pnl_by_sym[o.symbol].append(o.pnl_pct)
    if not pnl_by_sym:
        return "—", "—"
    avg = {s: sum(v) / len(v) for s, v in pnl_by_sym.items()}
    top  = max(avg, key=avg.__getitem__)
    worst = min(avg, key=avg.__getitem__)
    return f"{top}({avg[top]:+.1f}%)", f"{worst}({avg[worst]:+.1f}%)"


def send_weekly_review(
    notifier: TelegramNotifier,
    order_repo: V3PaperOrderRepository,
    perf_calc: V3PerformanceCalculator,
    strategy_id: str,
) -> None:
    try:
        stats   = perf_calc.calculate(strategy_id, "7d")
        all_ord = order_repo.load_all()
        week_ord = [o for o in all_ord if o.strategy_id == strategy_id]

        from datetime import UTC, datetime, timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds")
        week_ord = [o for o in week_ord if (o.created_at or "") >= cutoff]

        settled   = [o for o in week_ord if o.result in ("TP1", "TP2", "SL", "TIMEOUT")]
        long_cnt  = sum(1 for o in settled if o.direction == "LONG")
        short_cnt = sum(1 for o in settled if o.direction == "SHORT")
        max_win, max_loss = _streak(week_ord)
        top_sym, worst_sym = _top_worst(week_ord)

        msg = (
            f"📋 V3 Weekly Strategy Review\n"
            f"{'━' * 22}\n"
            f"策略   {strategy_id}\n"
            f"周期   过去 7 天\n"
            f"\n"
            f"📊 成交统计\n"
            f"  信号数   {stats.signals}\n"
            f"  推送数   {stats.pushed}\n"
            f"  成交数   {stats.filled}\n"
            f"  结算数   {stats.settled}\n"
            f"  TP1      {stats.tp1}  SL {stats.sl}\n"
            f"  超时     {stats.timeout}\n"
            f"\n"
            f"📈 绩效\n"
            f"  胜率     {stats.win_rate}%\n"
            f"  平均RR   {stats.avg_rr}\n"
            f"  平均PnL  {stats.avg_pnl}%\n"
            f"\n"
            f"🔀 方向\n"
            f"  LONG {long_cnt}  SHORT {short_cnt}\n"
            f"\n"
            f"🏆 连续\n"
            f"  最长连赢 {max_win}  最长连亏 {max_loss}\n"
            f"\n"
            f"🔍 标的\n"
            f"  最佳   {top_sym}\n"
            f"  最差   {worst_sym}\n"
            f"\n"
            f"💡 策略建议\n"
            f"  （仅分析，不自动修改参数）\n"
        )

        if stats.win_rate >= Decimal("60"):
            msg += "  ✅ 胜率良好，维持现有参数\n"
        elif stats.win_rate >= Decimal("40"):
            msg += "  ⚠️ 胜率中等，建议观察更多样本后再调整\n"
        elif stats.settled >= 3:
            msg += "  ❌ 胜率偏低，建议回顾过滤条件\n"
        else:
            msg += "  ℹ️ 样本量不足，继续观察\n"

        notifier.send(msg)
        log.info("[V3] weekly review sent for %s", strategy_id)
    except Exception:
        log.exception("[V3] weekly review failed")
