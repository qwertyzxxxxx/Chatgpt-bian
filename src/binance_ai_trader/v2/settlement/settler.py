"""V2 Settlement Engine — fill-check before TP/SL, writes order_events audit log.

Fill rules (identical to V1 paper/settler.py):
  LONG : candle.low  <= entry  → FILLED
  SHORT: candle.high >= entry  → FILLED

Only after fill is confirmed are TP1 / TP2 / SL evaluated.
EXPIRED_NOT_FILLED orders are excluded from the win-rate denominator.

Every state transition writes an append-only row to v2_order_events.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.v2.order_events.repository import (
    V2OrderEvent,
    V2OrderEventRepository,
    make_event_id,
)
from binance_ai_trader.v2.paper_portfolio.repository import V2PaperOrder, V2PaperOrderRepository

log = logging.getLogger(__name__)

_KLINE_INTERVAL = "15m"
_KLINE_LIMIT = 200


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _pnl_pct(direction: str, entry: Decimal, exit_price: Decimal) -> Decimal:
    sign = Decimal("1") if direction == "LONG" else Decimal("-1")
    return (exit_price - entry) / entry * Decimal("100") * sign


def _rr_realized(direction: str, entry: Decimal, stop_loss: Decimal, exit_price: Decimal) -> Decimal:
    risk = abs(entry - stop_loss)
    if risk == 0:
        return Decimal("0")
    return (exit_price - entry) / risk if direction == "LONG" else (entry - exit_price) / risk


def _duration_minutes(start_iso: str, end_iso: str) -> int:
    try:
        delta = _parse(end_iso) - _parse(start_iso)
        return max(0, int(delta.total_seconds() / 60))
    except Exception:
        return 0


class V2Settler:
    """Settles open V2 paper orders using 15-minute kline data."""

    def __init__(
        self,
        order_repo: V2PaperOrderRepository,
        event_repo: V2OrderEventRepository,
        client: BinancePublicClient,
    ) -> None:
        self._orders = order_repo
        self._events = event_repo
        self._client = client

    def settle_all(self) -> int:
        orders = self._orders.load_open()
        if not orders:
            return 0
        now = _utc_now()
        updated = 0
        for order in orders:
            try:
                if self._settle_one(order, now):
                    updated += 1
            except Exception as exc:
                log.warning("[V2] settler: %s %s failed: %s", order.symbol, order.order_id, exc)
        return updated

    def _settle_one(self, order: V2PaperOrder, now: datetime) -> bool:
        klines = self._client.klines(order.symbol, _KLINE_INTERVAL, limit=_KLINE_LIMIT)
        if not klines:
            return False

        created_ms = int(_parse(order.created_at).timestamp() * 1000)
        expires_dt = _parse(order.expires_at)
        relevant = [k for k in klines if k.close_time_ms > created_ms]
        if not relevant:
            return False

        if order.status == "OPEN":
            return self._check_fill(order, relevant, now, expires_dt)
        else:
            return self._check_settle(order, relevant, now, expires_dt)

    def _check_fill(self, order, candles, now, expires_dt) -> bool:
        for candle in candles:
            touched = (
                order.direction == "LONG" and candle.low <= order.entry
            ) or (
                order.direction == "SHORT" and candle.high >= order.entry
            )
            if touched:
                fill_dt = datetime.fromtimestamp(candle.close_time_ms / 1000, tz=UTC)
                filled_at = fill_dt.isoformat(timespec="seconds")
                self._orders.update_filled(order.order_id, filled_at)
                self._append_event(
                    order, "FILLED", "OPEN", "FILLED", candle.high, candle.low, filled_at
                )
                post_fill = [k for k in candles if k.close_time_ms > candle.close_time_ms]
                filled_order = V2PaperOrder(
                    order_id=order.order_id, signal_id=order.signal_id,
                    strategy_id=order.strategy_id, symbol=order.symbol,
                    direction=order.direction, entry=order.entry,
                    stop_loss=order.stop_loss, tp1=order.tp1, tp2=order.tp2,
                    rr=order.rr, status="FILLED", result=None,
                    created_at=order.created_at, filled_at=filled_at,
                    closed_at=None, expires_at=order.expires_at,
                    pnl_pct=None, rr_realized=None, duration_minutes=None,
                    pushed=order.pushed, metadata_json=order.metadata_json,
                )
                return self._check_settle(filled_order, post_fill, now, expires_dt)

        if now >= expires_dt:
            closed_at = now.isoformat(timespec="seconds")
            self._orders.update_settled(order.order_id, "EXPIRED_NOT_FILLED", closed_at, None, None, None)
            self._append_event(
                order, "EXPIRED_NOT_FILLED", "OPEN", "EXPIRED_NOT_FILLED", None, None, closed_at
            )
            return True
        return False

    def _check_settle(self, order, post_fill_candles, now, expires_dt) -> bool:
        tp1_hit = False
        for candle in post_fill_candles:
            close_dt = datetime.fromtimestamp(candle.close_time_ms / 1000, tz=UTC)
            candle_at = close_dt.isoformat(timespec="seconds")
            if order.direction == "LONG":
                if candle.low <= order.stop_loss:
                    return self._close(order, "SL", order.stop_loss, candle, candle_at)
                if candle.high >= order.tp2:
                    return self._close(order, "TP2", order.tp2, candle, candle_at)
                if candle.high >= order.tp1:
                    tp1_hit = True
            else:
                if candle.high >= order.stop_loss:
                    return self._close(order, "SL", order.stop_loss, candle, candle_at)
                if candle.low <= order.tp2:
                    return self._close(order, "TP2", order.tp2, candle, candle_at)
                if candle.low <= order.tp1:
                    tp1_hit = True

        if tp1_hit and post_fill_candles:
            last = post_fill_candles[-1]
            last_at = datetime.fromtimestamp(last.close_time_ms / 1000, tz=UTC).isoformat(timespec="seconds")
            return self._close(order, "TP1", order.tp1, last, last_at)

        if now >= expires_dt and post_fill_candles:
            last = post_fill_candles[-1]
            last_at = datetime.fromtimestamp(last.close_time_ms / 1000, tz=UTC).isoformat(timespec="seconds")
            return self._close(order, "TIMEOUT", last.close, last, last_at)

        return False

    def _close(self, order: V2PaperOrder, result: str, exit_price: Decimal, candle, closed_at: str) -> bool:
        pnl = _pnl_pct(order.direction, order.entry, exit_price)
        rr = _rr_realized(order.direction, order.entry, order.stop_loss, exit_price)
        ref = order.filled_at or order.created_at
        dur = _duration_minutes(ref, closed_at)
        self._orders.update_settled(order.order_id, result, closed_at, pnl, rr, dur)
        self._append_event(
            order, result, order.status, result, candle.high, candle.low, closed_at
        )
        return True

    def _append_event(
        self,
        order: V2PaperOrder,
        event_type: str,
        old_status: str | None,
        new_status: str,
        candle_high,
        candle_low,
        triggered_at: str,
    ) -> None:
        self._events.append(V2OrderEvent(
            event_id=make_event_id(),
            order_id=order.order_id,
            event_type=event_type,
            old_status=old_status,
            new_status=new_status,
            candle_high=Decimal(str(candle_high)) if candle_high is not None else None,
            candle_low=Decimal(str(candle_low)) if candle_low is not None else None,
            triggered_at=triggered_at,
            metadata_json="{}",
        ))
