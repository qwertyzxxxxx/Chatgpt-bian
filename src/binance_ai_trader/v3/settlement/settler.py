"""V3 Settler — fill-first settlement for all V3 paper orders.

Logic (same as V2, applied to v3_paper_orders):
  OPEN   → check if low <= entry (LONG) or high >= entry (SHORT) → FILLED
  FILLED → check if high >= tp1 (LONG TP1), low <= sl (SL), or expires → CLOSED
  OPEN   → if expires_at < now and not filled → EXPIRED_NOT_FILLED

Uses 15m klines from BinancePublicClient for each open/filled order.
Every state transition writes to v3_order_events for full audit trail.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v3.paper.repository import (
    V3OrderEvent,
    V3PaperOrder,
    V3PaperOrderRepository,
    make_event_id,
)
from binance_ai_trader.v3.telegram.labels import strategy_tag

log = logging.getLogger(__name__)

_INTERVAL = "15m"
_KLINE_LIMIT = 4


class V3Settler:
    def __init__(
        self,
        order_repo: V3PaperOrderRepository,
        client: BinancePublicClient,
        notifier: TelegramNotifier | None = None,
        live_repo: object | None = None,
    ) -> None:
        self._order_repo = order_repo
        self._client = client
        self._notifier = notifier
        # Optional LiveOrderRepository — used only to detect and flag when
        # the paper/shadow settlement result (TP1/SL) diverges from the real
        # exchange position's status, so the push message never implies a
        # real position closed when it is actually still open.
        self._live_repo = live_repo

    def settle_all(self, strategy_id: str | None = None) -> int:
        if strategy_id is not None:
            open_orders = self._order_repo.load_open_by_strategy(strategy_id)
        else:
            open_orders = self._order_repo.load_open()
        if not open_orders:
            return 0

        updated = 0
        now = datetime.now(UTC)

        for order in open_orders:
            try:
                changed = self._settle_one(order, now)
                if changed:
                    updated += 1
            except Exception:
                log.exception("[V3] settle error for order %s", order.order_id)

        log.info("[V3] settlement pass done (strategy=%s): %d updated", strategy_id or "all", updated)
        return updated

    def _settle_one(self, order: V3PaperOrder, now: datetime) -> bool:
        # ── OPEN: check fill or expiry ─────────────────────────────────
        if order.status == "OPEN":
            expires = datetime.fromisoformat(order.expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)

            if now >= expires:
                closed_at = now.isoformat(timespec="seconds")
                self._order_repo.update_expired_not_filled(order.order_id, closed_at)
                self._order_repo.append_event(V3OrderEvent(
                    event_id=make_event_id(),
                    order_id=order.order_id,
                    signal_id=order.signal_id,
                    event_type="EXPIRED_NOT_FILLED",
                    old_status="OPEN",
                    new_status="CLOSED",
                    candle_high=None,
                    candle_low=None,
                    triggered_at=closed_at,
                    metadata_json="{}",
                ))
                log.info("[V3] %s EXPIRED_NOT_FILLED", order.order_id)
                return True

            klines = self._fetch_klines(order.symbol)
            if not klines:
                return False

            for k in klines:
                high, low = k["high"], k["low"]
                filled = (
                    low <= order.entry if order.direction == "LONG"
                    else high >= order.entry
                )
                if filled:
                    filled_at = now.isoformat(timespec="seconds")
                    self._order_repo.update_filled(order.order_id, filled_at)
                    self._order_repo.append_event(V3OrderEvent(
                        event_id=make_event_id(),
                        order_id=order.order_id,
                        signal_id=order.signal_id,
                        event_type="FILLED",
                        old_status="OPEN",
                        new_status="FILLED",
                        candle_high=high,
                        candle_low=low,
                        triggered_at=filled_at,
                        metadata_json="{}",
                    ))
                    log.info("[V3] %s FILLED at %s", order.order_id, order.entry)
                    return True

        # ── FILLED: check TP1 / SL / TIMEOUT ──────────────────────────
        if order.status == "FILLED":
            expires = datetime.fromisoformat(order.expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)

            klines = self._fetch_klines(order.symbol)
            if not klines:
                return False

            for k in klines:
                high, low = k["high"], k["low"]

                if order.direction == "LONG":
                    tp_hit = high >= order.tp1
                    sl_hit = low <= order.stop_loss
                else:
                    tp_hit = low <= order.tp1
                    sl_hit = high >= order.stop_loss

                if tp_hit and sl_hit:
                    result = self._resolve_same_candle(order, k)
                    return self._close(order, result, now, high, low)
                if tp_hit:
                    return self._close(order, "TP1", now, high, low)
                if sl_hit:
                    return self._close(order, "SL", now, high, low)

            if now >= expires:
                return self._close(order, "TIMEOUT", now, None, None)

        return False

    def _close(
        self,
        order: V3PaperOrder,
        result: str,
        now: datetime,
        candle_high: Decimal | None,
        candle_low: Decimal | None,
    ) -> bool:
        closed_at = now.isoformat(timespec="seconds")
        pnl_pct, rr_realized = _calc_pnl(order, result)
        self._order_repo.update_settled(
            order.order_id, result, closed_at, pnl_pct, rr_realized
        )
        self._order_repo.append_event(V3OrderEvent(
            event_id=make_event_id(),
            order_id=order.order_id,
            signal_id=order.signal_id,
            event_type=result,
            old_status="FILLED",
            new_status="CLOSED",
            candle_high=candle_high,
            candle_low=candle_low,
            triggered_at=closed_at,
            metadata_json="{}",
        ))
        log.info(
            "[V3] %s %s %s pnl=%s rr=%s",
            order.order_id, order.symbol, result, pnl_pct, rr_realized,
        )
        if self._notifier is not None:
            try:
                live_note = self._live_divergence_note(order)
                self._notifier.send(
                    _fmt_settlement_msg(order, result, pnl_pct, rr_realized, closed_at, live_note)
                )
            except Exception:
                log.exception("[V3] failed to send settlement notification for %s", order.order_id)
        self._record_paper_vs_live(order, result, closed_at)
        return True

    def _record_paper_vs_live(self, order: V3PaperOrder, result: str, closed_at: str) -> None:
        """Log this settlement alongside the real live order state, purely as
        a diff record for future paper-strategy tuning. Never affects paper
        stats or live trading — best-effort, swallows all errors."""
        if self._live_repo is None:
            return
        try:
            live_order = self._live_repo.load_by_signal_id(order.signal_id)
            live_status = live_order.status if live_order else None
            live_closed_at = live_order.updated_at if live_order else None
            match = (
                (result == "TP1" and live_status == "CLOSED_TP")
                or (result == "SL" and live_status == "CLOSED_SL")
                or (live_status is None)
            )
            self._live_repo.record_paper_vs_live(
                signal_id=order.signal_id,
                strategy_id=order.strategy_id,
                symbol=order.symbol,
                paper_result=result,
                paper_closed_at=closed_at,
                live_status=live_status,
                live_closed_at=live_closed_at,
                match=match,
            )
        except Exception:
            log.exception("[V3] failed to record paper-vs-live comparison for %s", order.signal_id)

    def _live_divergence_note(self, order: V3PaperOrder) -> str | None:
        """If this strategy mirrors real trades, check whether the actual
        exchange position has already closed with the same outcome. If the
        real position is still open while the paper/shadow result says
        TP1/SL, return a clarifying note so the push never implies the real
        account already closed the trade.
        """
        if self._live_repo is None:
            return None
        try:
            live_order = self._live_repo.load_by_signal_id(order.signal_id)
        except Exception:
            log.exception("[V3] live status lookup failed for %s", order.signal_id)
            return None
        if live_order is None:
            return None
        if live_order.status in ("CLOSED_TP", "CLOSED_SL", "CLOSED", "MANUAL_CLOSED"):
            return None
        if live_order.status in ("FILLED", "PENDING"):
            return "⚠️ 以上为模拟结算结果，实盘仓位目前仍在持仓中，尚未触发止盈/止损"
        return None

    def _fetch_klines(self, symbol: str) -> list[dict]:
        try:
            raw = self._client.klines(symbol, _INTERVAL, limit=_KLINE_LIMIT)
            return [
                {"high": k.high, "low": k.low, "open_time_ms": k.open_time_ms, "close_time_ms": k.close_time_ms}
                for k in raw
            ]
        except Exception as exc:
            log.warning("[V3] kline fetch failed for %s: %s", symbol, exc)
            return []

    def _resolve_same_candle(self, order: V3PaperOrder, k: dict) -> str:
        """Both TP1 and SL fell within the same 15m candle's range — the 15m
        OHLC alone cannot tell us which was touched first. Fetch 1m klines
        covering this exact window and walk them in chronological order to
        find the true first touch. If 1m data is unavailable/inconclusive,
        fall back to SL (conservative — matches worst-case risk assumption).
        """
        try:
            open_ms = k.get("open_time_ms")
            close_ms = k.get("close_time_ms")
            if open_ms is None or close_ms is None:
                raise ValueError("missing candle window bounds")
            one_min = self._client.klines(
                order.symbol, "1m", limit=16, start_time_ms=open_ms, end_time_ms=close_ms
            )
            for mk in one_min:
                if order.direction == "LONG":
                    tp_hit = mk.high >= order.tp1
                    sl_hit = mk.low <= order.stop_loss
                else:
                    tp_hit = mk.low <= order.tp1
                    sl_hit = mk.high >= order.stop_loss
                if tp_hit and sl_hit:
                    continue
                if tp_hit:
                    return "TP1"
                if sl_hit:
                    return "SL"
        except Exception as exc:
            log.warning(
                "[V3] same-candle TP1/SL tie-break failed for %s, defaulting to SL: %s",
                order.symbol, exc,
            )
        log.info("[V3] %s same-candle TP1/SL ambiguous — defaulting to SL", order.order_id)
        return "SL"


def _sdt(iso: str | None) -> str:
    """Format ISO timestamp as MM-DD HH:MM (UTC)."""
    if not iso:
        return "—"
    s = iso.replace("Z", "").replace("+00:00", "")
    return s[5:16].replace("T", " ")


def _holding(filled_at: str | None, closed_at: str | None) -> str:
    if not filled_at or not closed_at:
        return "—"
    try:
        fmt = "%Y-%m-%dT%H:%M:%S"
        s = datetime.strptime(filled_at[:19], fmt)
        e = datetime.strptime(closed_at[:19], fmt)
        secs = int((e - s).total_seconds())
        h, rem = divmod(max(0, secs), 3600)
        m = rem // 60
        return f"{h}h{m:02d}m" if h else f"{m}m"
    except Exception:
        return "—"


def _fmt_settlement_msg(
    order: V3PaperOrder,
    result: str,
    pnl_pct: Decimal,
    rr_realized: Decimal,
    closed_at: str,
    live_note: str | None = None,
) -> str:
    _EMOJI = {"TP1": "✅", "SL": "❌", "TIMEOUT": "⏰"}
    emoji = _EMOJI.get(result, "📋")
    sign = "+" if pnl_pct >= 0 else ""
    pnl_str = f"{sign}{pnl_pct:.2f}%"
    sl_pct = abs(order.entry - order.stop_loss) / order.entry * Decimal("100")
    sl_sign = "-" if order.direction == "LONG" else "+"
    note = f"\n{live_note}" if live_note else ""
    return (
        f"[{strategy_tag(order.strategy_id)}] {emoji} 结算 {result}  {pnl_str}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{order.signal_id}\n"
        f"{order.symbol}  {order.direction}\n"
        f"买入价  {order.entry}\n"
        f"止损价  {order.stop_loss}  ({sl_sign}{sl_pct:.1f}%)\n"
        f"TP1    {order.tp1}  RR {rr_realized}\n"
        f"入场    {_sdt(order.filled_at)}\n"
        f"平仓    {_sdt(closed_at)}\n"
        f"持仓    {_holding(order.filled_at, closed_at)}"
        f"{note}"
    )


def _calc_pnl(order: V3PaperOrder, result: str) -> tuple[Decimal, Decimal]:
    ZERO = Decimal("0")
    if result == "TP1":
        price = order.tp1
    elif result == "SL":
        price = order.stop_loss
    elif result == "TIMEOUT":
        price = order.entry
    else:
        return ZERO, ZERO

    if order.direction == "LONG":
        pnl = (price - order.entry) / order.entry * Decimal("100")
    else:
        pnl = (order.entry - price) / order.entry * Decimal("100")

    risk = abs(order.entry - order.stop_loss)
    rr = (price - order.entry) / risk if risk > 0 else ZERO
    if order.direction == "SHORT":
        rr = (order.entry - price) / risk if risk > 0 else ZERO

    return pnl.quantize(Decimal("0.01")), rr.quantize(Decimal("0.01"))
