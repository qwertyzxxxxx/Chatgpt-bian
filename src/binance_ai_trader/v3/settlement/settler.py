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
from binance_ai_trader.v3.paper.repository import (
    V3OrderEvent,
    V3PaperOrder,
    V3PaperOrderRepository,
    make_event_id,
)

log = logging.getLogger(__name__)

_INTERVAL = "15m"
_KLINE_LIMIT = 4


class V3Settler:
    def __init__(
        self,
        order_repo: V3PaperOrderRepository,
        client: BinancePublicClient,
    ) -> None:
        self._order_repo = order_repo
        self._client = client

    def settle_all(self) -> int:
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

        log.info("[V3] settlement pass done: %d updated", updated)
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
                    if high >= order.tp1:
                        return self._close(order, "TP1", now, high, low)
                    if low <= order.stop_loss:
                        return self._close(order, "SL", now, high, low)
                else:
                    if low <= order.tp1:
                        return self._close(order, "TP1", now, high, low)
                    if high >= order.stop_loss:
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
        return True

    def _fetch_klines(self, symbol: str) -> list[dict]:
        try:
            raw = self._client.klines(symbol, _INTERVAL, limit=_KLINE_LIMIT)
            return [{"high": k.high, "low": k.low} for k in raw]
        except Exception as exc:
            log.warning("[V3] kline fetch failed for %s: %s", symbol, exc)
            return []


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
