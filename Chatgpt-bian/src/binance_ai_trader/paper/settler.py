"""Unified paper order settler — fill-check before TP/SL evaluation.

Fill rules:
  LONG : candle.low  <= entry  (price dips to limit buy)
  SHORT: candle.high >= entry  (price pops to limit sell/short)

Only after fill is confirmed are TP1 / TP2 / SL evaluated.
Orders where entry is never touched become EXPIRED_NOT_FILLED and are
excluded from the win-rate denominator.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.paper.order_repository import PaperOrder, PaperOrderRepository

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://fapi.binance.com"
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


def _rr(direction: str, entry: Decimal, stop_loss: Decimal, exit_price: Decimal) -> Decimal:
    risk = abs(entry - stop_loss)
    if risk == 0:
        return Decimal("0")
    if direction == "LONG":
        return (exit_price - entry) / risk
    return (entry - exit_price) / risk


def _duration_minutes(created_at: str, closed_at: str) -> int:
    try:
        delta = _parse(closed_at) - _parse(created_at)
        return max(0, int(delta.total_seconds() / 60))
    except Exception:
        return 0


class PaperOrderSettler:
    """Settles open paper_orders using 15-minute kline data from Binance."""

    def __init__(
        self,
        repository: PaperOrderRepository,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 10.0,
    ) -> None:
        self._repo = repository
        self._client = BinancePublicClient(base_url=base_url, timeout=timeout)

    def settle_all(self) -> int:
        """Settle all OPEN/FILLED orders. Returns number of orders updated."""
        orders = self._repo.load_open()
        if not orders:
            return 0
        now = _utc_now()
        updated = 0
        for order in orders:
            try:
                changed = self._settle_one(order, now)
                if changed:
                    updated += 1
            except Exception as exc:
                log.warning("settler: failed to settle %s %s: %s", order.symbol, order.order_id, exc)
        return updated

    def _settle_one(self, order: PaperOrder, now: datetime) -> bool:
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

    def _check_fill(
        self, order: PaperOrder, candles, now: datetime, expires_dt: datetime
    ) -> bool:
        """Check if entry was touched (fills the order)."""
        filled_candle = None
        filled_at_ms = None

        for candle in candles:
            if order.direction == "LONG":
                if candle.low <= order.entry:
                    filled_candle = candle
                    filled_at_ms = candle.close_time_ms
                    break
            else:
                if candle.high >= order.entry:
                    filled_candle = candle
                    filled_at_ms = candle.close_time_ms
                    break

        if filled_candle is None:
            if now >= expires_dt:
                self._expire_not_filled(order, now.isoformat(timespec="seconds"))
                return True
            return False

        fill_dt = datetime.fromtimestamp(filled_at_ms / 1000, tz=UTC)
        filled_at = fill_dt.isoformat(timespec="seconds")
        self._repo.update_filled(order.order_id, filled_at)

        post_fill = [k for k in candles if k.close_time_ms > filled_at_ms]
        order_filled = PaperOrder(
            order_id=order.order_id,
            strategy_id=order.strategy_id,
            source_type=order.source_type,
            source_id=order.source_id,
            symbol=order.symbol,
            direction=order.direction,
            entry=order.entry,
            stop_loss=order.stop_loss,
            tp1=order.tp1,
            tp2=order.tp2,
            rr=order.rr,
            status="FILLED",
            result=None,
            pushed=order.pushed,
            alert_id=order.alert_id,
            created_at=order.created_at,
            filled_at=filled_at,
            closed_at=None,
            expires_at=order.expires_at,
            pnl_pct=None,
            rr_realized=None,
            duration_minutes=None,
            legacy=order.legacy,
        )
        return self._check_settle(order_filled, post_fill, now, expires_dt)

    def _check_settle(
        self, order: PaperOrder, post_fill_candles, now: datetime, expires_dt: datetime
    ) -> bool:
        """Evaluate TP1 / TP2 / SL from candles after fill."""
        tp1_hit = False

        for candle in post_fill_candles:
            if order.direction == "LONG":
                if candle.low <= order.stop_loss:
                    return self._close(order, "SL", order.stop_loss, candle)
                if candle.high >= order.tp2:
                    return self._close(order, "TP2", order.tp2, candle)
                if candle.high >= order.tp1:
                    tp1_hit = True
            else:
                if candle.high >= order.stop_loss:
                    return self._close(order, "SL", order.stop_loss, candle)
                if candle.low <= order.tp2:
                    return self._close(order, "TP2", order.tp2, candle)
                if candle.low <= order.tp1:
                    tp1_hit = True

        if tp1_hit:
            last = post_fill_candles[-1]
            return self._close(order, "TP1", order.tp1, last)

        if now >= expires_dt and post_fill_candles:
            last = post_fill_candles[-1]
            return self._close(order, "TIMEOUT", last.close, last)

        return False

    def _close(self, order: PaperOrder, result: str, exit_price: Decimal, candle) -> bool:
        closed_at = datetime.fromtimestamp(
            candle.close_time_ms / 1000, tz=UTC
        ).isoformat(timespec="seconds")
        pnl = _pnl_pct(order.direction, order.entry, exit_price)
        rr = _rr(order.direction, order.entry, order.stop_loss, exit_price)
        ref_at = order.filled_at or order.created_at
        dur = _duration_minutes(ref_at, closed_at)
        self._repo.update_settled(
            order.order_id, result, closed_at, pnl, rr, dur
        )
        return True

    def _expire_not_filled(self, order: PaperOrder, closed_at: str) -> None:
        self._repo.update_settled(
            order.order_id, "EXPIRED_NOT_FILLED", closed_at, None, None, None
        )
