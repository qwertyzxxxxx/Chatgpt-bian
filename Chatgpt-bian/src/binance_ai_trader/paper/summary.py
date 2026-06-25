"""6-hour Telegram summary for paper_orders (pushed=True only)."""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.parse
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from binance_ai_trader.paper.order_repository import PaperOrder, PaperOrderRepository

log = logging.getLogger(__name__)

_WIN_RESULTS = frozenset({"TP1", "TP2"})
_SETTLED_RESULTS = frozenset({"TP1", "TP2", "SL"})
_MAX_CHARS = 4096


def _fmt_pnl(pnl: Decimal | None) -> str:
    if pnl is None:
        return "—"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{float(pnl):.2f}%"


def _fmt_rr(rr: Decimal | None) -> str:
    if rr is None:
        return "—"
    return f"{float(rr):.2f}"


def build_summary(
    repo: PaperOrderRepository,
    window_hours: int = 6,
) -> str:
    now = datetime.now(UTC)
    since = (now - timedelta(hours=window_hours)).isoformat(timespec="seconds")
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    pushed = repo.load_pushed_since(since)
    recent7 = repo.load_recent_pushed(n=7)

    total = len(pushed)
    filled = sum(1 for o in pushed if o.status not in ("OPEN", "EXPIRED_NOT_FILLED"))
    not_filled = sum(1 for o in pushed if o.status == "EXPIRED_NOT_FILLED")
    tp1 = sum(1 for o in pushed if o.result == "TP1")
    tp2 = sum(1 for o in pushed if o.result == "TP2")
    sl = sum(1 for o in pushed if o.result == "SL")
    open_count = sum(1 for o in pushed if o.status in ("OPEN", "FILLED"))

    settled = [o for o in pushed if o.result in _SETTLED_RESULTS]
    wins = [o for o in settled if o.result in _WIN_RESULTS]
    win_rate = round(len(wins) / len(settled) * 100) if settled else 0

    rrs = [o.rr_realized for o in settled if o.rr_realized is not None]
    avg_rr = round(sum(float(r) for r in rrs) / len(rrs), 2) if rrs else 0.0

    pnls = [o.pnl_pct for o in settled if o.pnl_pct is not None]
    avg_pnl = round(sum(float(p) for p in pnls) / len(pnls), 2) if pnls else 0.0
    pnl_sign = "+" if avg_pnl >= 0 else ""

    lines = [
        f"📊 模拟仓{window_hours}小时汇总（{now_str}）",
        "",
        "Hotlist 推送订单：",
        f"  推送 {total}",
        f"  已成交 {filled}  未成交 {not_filled}  持仓中 {open_count}",
        f"  TP1 {tp1}  TP2 {tp2}  SL {sl}",
        f"  胜率 {win_rate}%  平均RR {avg_rr:+.2f}  平均收益 {pnl_sign}{avg_pnl:.2f}%",
        "",
    ]

    if recent7:
        lines.append("最近7条推送订单：")
        for o in recent7:
            status_str = o.result or o.status
            pnl_str = _fmt_pnl(o.pnl_pct)
            dur = f"{o.duration_minutes}min" if o.duration_minutes else "—"
            lines.append(
                f"  {o.symbol} {o.direction} @ {o.entry}"
                f"  [{status_str}] {pnl_str}  {dur}"
            )
        lines.append("")

    all_candidate = repo.load_all(strategy_id="hotlist", pushed=False, limit=9999)
    candidate_open = sum(1 for o in all_candidate if o.status in ("OPEN", "FILLED"))
    candidate_settled = [o for o in all_candidate if o.result in _SETTLED_RESULTS]
    candidate_wins = [o for o in candidate_settled if o.result in _WIN_RESULTS]
    c_win_rate = round(len(candidate_wins) / len(candidate_settled) * 100) if candidate_settled else 0

    lines += [
        f"内部候选池：{len(all_candidate)}条，仅Dashboard查看",
        f"  (候选池胜率 {c_win_rate}%  结算 {len(candidate_settled)}笔)",
        "",
        "仅供研究 | 不进行实盘交易",
    ]
    return "\n".join(lines)


def send_summary(
    repo: PaperOrderRepository,
    bot_token: str,
    chat_id: str,
    window_hours: int = 6,
    timeout: int = 10,
) -> bool:
    text = build_summary(repo, window_hours=window_hours)
    chunks = [text[i:i + _MAX_CHARS] for i in range(0, len(text), _MAX_CHARS)]
    ok = True
    for chunk in chunks:
        payload = json.dumps({"chat_id": chat_id, "text": chunk}).encode()
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout):
                pass
        except Exception as exc:
            log.warning("paper summary: Telegram send failed: %s", exc)
            ok = False
    return ok
