"""V3 Shadow Report — hourly portfolio status across all V3 strategies.

Fixed format (do not change without explicit instruction):

  📊 V3 纸盘 Paper  /  📊 V66 纸盘 Paper

  【累计 All Time】
  【30日 30d】
  【今日 Today】
  【当前挂单 Pending】
  【当前持仓 Filled】
  【最近结算 Last 7】
  【System】

Each section now filters by strategy_id — no duplicate order listing across
the V3 and V66 hourly reports.
All time durations computed in real-time from created_at/filled_at/closed_at.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v3.paper.repository import V3PaperOrder, V3PaperOrderRepository
from binance_ai_trader.v3.performance.calculator import V3PerformanceCalculator, V3Stats

log = logging.getLogger(__name__)

_STRAT_LABEL: dict[str, str] = {
    "hotlist_momentum_v3": "V3",
    "hotlist_v66":         "V66",
}
_STRAT_TITLE: dict[str, str] = {
    "hotlist_momentum_v3": "📊 V3 纸盘 Paper",
    "hotlist_v66":         "📊 V66 纸盘 Paper",
}


# ── time helpers ──────────────────────────────────────────────────────────────

def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def _fmt_delta(seconds: int) -> str:
    seconds = max(0, seconds)
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


def _elapsed(iso: str | None) -> str:
    dt = _parse_iso(iso)
    if dt is None:
        return "—"
    return _fmt_delta(int((datetime.now(UTC) - dt).total_seconds()))


def _between(start: str | None, end: str | None) -> str:
    s, e = _parse_iso(start), _parse_iso(end)
    if s is None or e is None:
        return "—"
    return _fmt_delta(int((e - s).total_seconds()))


def _short_dt(iso: str | None) -> str:
    """Return 'MM-DD HH:MM' (drop year to save space)."""
    return iso[5:16].replace("T", " ") if iso else "—"


def _pnl_str(pnl: Decimal | None) -> str:
    if pnl is None:
        return "—"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.2f}%"


def _seq(signal_id: str) -> str:
    """'HOT-20260708-000309' → '#309'"""
    try:
        return "#" + str(int(signal_id.rsplit("-", 1)[-1]))
    except (ValueError, IndexError):
        return signal_id


def _strat(strategy_id: str) -> str:
    return _STRAT_LABEL.get(strategy_id, strategy_id[:4])


# ── price helpers ─────────────────────────────────────────────────────────────

def _fetch_prices(
    client: BinancePublicClient | None,
    symbols: list[str],
) -> dict[str, Decimal]:
    if client is None or not symbols:
        return {}
    try:
        tickers = client.tickers_24h()
        return {t.symbol: t.last_price for t in tickers if t.symbol in symbols}
    except Exception:
        return {}


def _current_pnl(order: V3PaperOrder, price_map: dict[str, Decimal]) -> str:
    price = price_map.get(order.symbol)
    if price is None:
        return "—"
    try:
        if order.direction == "LONG":
            pnl = (price - order.entry) / order.entry * Decimal("100")
        else:
            pnl = (order.entry - price) / order.entry * Decimal("100")
        return _pnl_str(pnl.quantize(Decimal("0.01")))
    except Exception:
        return "—"


# ── reporter ──────────────────────────────────────────────────────────────────

class V3ShadowReporter:
    def __init__(
        self,
        notifier: TelegramNotifier,
        order_repo: V3PaperOrderRepository,
        perf_calc: V3PerformanceCalculator,
        strategy_id: str,
        client: BinancePublicClient | None = None,
        scan_interval_minutes: int = 15,
        settle_interval_minutes: int = 15,
        summary_interval_hours: int = 1,
    ) -> None:
        self._notifier = notifier
        self._order_repo = order_repo
        self._perf_calc = perf_calc
        self._strategy_id = strategy_id
        self._client = client
        self._scan_min = scan_interval_minutes
        self._settle_min = settle_interval_minutes
        self._summary_h = summary_interval_hours

    def send_report(self) -> None:
        try:
            msg = self._build_message()
            self._notifier.send(msg)
            log.info("[V3] shadow report sent")
        except Exception:
            log.exception("[V3] failed to send shadow report")
            raise

    def _build_message(self) -> str:
        alltime = self._perf_calc.calculate(self._strategy_id, "all_time")
        stats30d = self._perf_calc.calculate(self._strategy_id, "30d")
        today    = self._perf_calc.calculate(self._strategy_id, "today")

        sid = self._strategy_id
        open_orders    = self._order_repo.load_open_by_strategy(sid)
        recent_settled = [
            o for o in self._order_repo.load_recent_settled(14)
            if o.strategy_id == sid
        ][:7]

        filled  = [o for o in open_orders if o.status == "FILLED"]
        pending = [o for o in open_orders if o.status == "OPEN"]

        price_map = _fetch_prices(self._client, [o.symbol for o in filled])

        title = _STRAT_TITLE.get(sid, "📊 Paper Portfolio")
        parts = [
            f"{title}\n",
            _stats_section("累计 All Time", alltime),
            _stats_section("30日 30d", stats30d),
            _today_section(today),
            _pending_section(pending),
            _positions_section(filled, price_map),
            _settled_section(recent_settled),
            _system_section(self._scan_min, self._settle_min, self._summary_h),
        ]
        return "\n".join(parts)


# ── section builders ──────────────────────────────────────────────────────────

def _stats_section(label: str, s: V3Stats) -> str:
    return (
        f"【{label}】\n"
        f"推送/成交/结算：{s.pushed}/{s.filled}/{s.settled}\n"
        f"TP1 {s.tp1}  SL {s.sl}  超时 {s.timeout}  胜率 {s.win_rate}%\n"
        f"Avg RR {s.avg_rr}  Avg PnL {s.avg_pnl}%"
    )


def _today_section(s: V3Stats) -> str:
    return (
        "\n【今日 Today】\n"
        f"推送 {s.pushed}  成交 {s.filled}  "
        f"TP1 {s.tp1}  SL {s.sl}  超时 {s.timeout}"
    )


def _pending_section(orders: list[V3PaperOrder]) -> str:
    if not orders:
        return "\n【当前挂单 Pending】\n共 0 笔"
    rows = [f"\n【当前挂单 Pending】\n共 {len(orders)} 笔"]
    for o in orders:
        tag = _strat(o.strategy_id)
        rows.append(
            f"  [{tag}] {o.symbol} {o.direction} {_seq(o.signal_id)}  @{o.entry}\n"
            f"  等待 {_elapsed(o.created_at)}  到期 {_short_dt(o.expires_at)}"
        )
    return "\n".join(rows)


def _positions_section(
    orders: list[V3PaperOrder],
    price_map: dict[str, Decimal],
) -> str:
    if not orders:
        return "\n【当前持仓 Filled】\n共 0 笔"
    rows = [f"\n【当前持仓 Filled】\n共 {len(orders)} 笔"]
    for o in orders:
        tag = _strat(o.strategy_id)
        rows.append(
            f"  [{tag}] {o.symbol} {o.direction} {_seq(o.signal_id)}  @{o.entry}\n"
            f"  PnL {_current_pnl(o, price_map)}  持仓 {_elapsed(o.filled_at)}"
        )
    return "\n".join(rows)


def _settled_section(orders: list[V3PaperOrder]) -> str:
    if not orders:
        return "\n【最近结算 Last 7】\n暂无"
    rows = [f"\n【最近结算 Last 7】\n共 {len(orders)} 笔"]
    _ICON = {"TP1": "✅", "TP2": "✅", "SL": "❌", "TIMEOUT": "⏰"}
    for o in orders:
        tag  = _strat(o.strategy_id)
        icon = _ICON.get(o.result or "", "📋")
        dur  = _between(o.filled_at, o.closed_at)
        rows.append(
            f"  {icon}[{tag}] {o.symbol} {o.direction} {_seq(o.signal_id)}  "
            f"{o.result} {_pnl_str(o.pnl_pct)}  {dur}\n"
            f"  入{_short_dt(o.filled_at)} → 平{_short_dt(o.closed_at)}  @{o.entry}"
        )
    return "\n".join(rows)


def _system_section(scan_min: int, settle_min: int, summary_h: int) -> str:
    return (
        "\n【System】\n"
        f"扫描 {scan_min}min  结算 {settle_min}min  汇报 {summary_h}h"
    )
