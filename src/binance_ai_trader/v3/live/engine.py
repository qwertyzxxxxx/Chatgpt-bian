"""Live Mirror Engine — risk checks, order placement, sync loop.

Flow per signal:
  1. try_place(candidate) — risk check → set leverage → place LIMIT entry
  2. Return PlaceResult(ok, reason) used to prefix Telegram message
  3. Sync task (every 3 min):
       PENDING order: check if entry filled → if yes, place SL + TP (with retry)
       FILLED order:  check if SL/TP triggered → close DB record
       Naked position check: re-attach SL/TP using real Binance position qty
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.notifications import TelegramNotifier
from binance_ai_trader.v3.candidates.repository import V3Candidate
from binance_ai_trader.v3.live.client import BinanceFuturesClient, BinanceFuturesError
from binance_ai_trader.v3.live.models import (
    LiveEvent,
    LiveOrder,
    PlaceResult,
    make_live_event_id,
    make_live_order_id,
)
from binance_ai_trader.v3.live.repository import LiveOrderRepository

log = logging.getLogger(__name__)

_MAX_SL_PCT          = Decimal("10")  # SL must be ≤10% from entry


class LiveMirrorEngine:
    def __init__(
        self,
        client: BinanceFuturesClient,
        repo: LiveOrderRepository,
        notifier: TelegramNotifier | None = None,
        notional_usdt: Decimal = Decimal("1000"),
        max_pending: int = 10,
        max_positions: int = 5,
    ) -> None:
        self._client   = client
        self._repo     = repo
        self._notifier = notifier
        self._notional = notional_usdt
        self._max_pend = max_pending
        self._max_pos  = max_positions

    def is_enabled(self) -> bool:
        return os.environ.get("LIVE_TRADING_ENABLED", "").lower() == "true"

    # ── Place ──────────────────────────────────────────────────────────────────

    def try_place(self, candidate: V3Candidate) -> PlaceResult:
        if not self.is_enabled():
            return PlaceResult(ok=False, reason="LIVE_TRADING_ENABLED!=true")

        now = datetime.now(UTC).isoformat(timespec="seconds")
        live_order_id = make_live_order_id()

        try:
            reason = self._risk_check(candidate)
            if reason:
                self._save_rejected(live_order_id, candidate, now, reason)
                return PlaceResult(ok=False, reason=reason, live_order_id=live_order_id)

            entry = Decimal(candidate.entry)
            sl    = Decimal(candidate.sl)
            tp    = Decimal(candidate.tp1)
            side  = "BUY" if candidate.direction == "LONG" else "SELL"
            pos_side = candidate.direction  # "LONG" or "SHORT"

            leverage = self._choose_leverage(candidate.symbol)
            try:
                self._client.set_leverage(candidate.symbol, leverage)
            except BinanceFuturesError as exc:
                log.warning("[Live] set_leverage failed for %s: %s", candidate.symbol, exc)

            qty = self._calc_quantity(candidate.symbol, entry, leverage)
            if qty <= 0:
                reason = "数量计算结果≤0"
                self._save_rejected(live_order_id, candidate, now, reason)
                return PlaceResult(ok=False, reason=reason, live_order_id=live_order_id)

            entry_r = self._client.round_price(candidate.symbol, entry)
            sl_r    = self._client.round_price(candidate.symbol, sl)
            tp_r    = self._client.round_price(candidate.symbol, tp)

            # Try OTOCO first (entry + SL + TP in one call → zero naked-position window)
            bn_order_id: str = ""
            sl_order_id: str | None = None
            tp_order_id: str | None = None
            otoco_ok = False
            try:
                otoco = self._client.place_otoco(
                    candidate.symbol, side, entry_r, qty, tp_r, sl_r, position_side=pos_side
                )
                bn_order_id = otoco["entry_order_id"]
                sl_order_id = otoco["sl_order_id"] or None
                tp_order_id = otoco["tp_order_id"] or None
                otoco_ok = True
                log.info("[Live] OTOCO placed %s %s %s entry=%s sl=%s tp=%s",
                         live_order_id, candidate.symbol, side, entry_r, sl_r, tp_r)
            except BinanceFuturesError as exc:
                log.warning("[Live] OTOCO failed for %s (%s), falling back to plain LIMIT: %s",
                            candidate.symbol, exc.code, exc.msg)

            if not otoco_ok:
                resp = self._client.place_limit(candidate.symbol, side, entry_r, qty, position_side=pos_side)
                bn_order_id = str(resp.get("orderId", ""))

            order = LiveOrder(
                live_order_id  = live_order_id,
                signal_id      = candidate.signal_id,
                symbol         = candidate.symbol,
                side           = side,
                direction      = candidate.direction,
                entry          = str(entry_r),
                sl             = str(sl),
                tp             = str(tp),
                notional       = str(self._notional),
                leverage       = leverage,
                quantity       = str(qty),
                status         = "PENDING",
                entry_order_id = bn_order_id,
                sl_order_id    = sl_order_id,
                tp_order_id    = tp_order_id,
                created_at     = now,
                updated_at     = now,
                reject_reason  = None,
            )
            self._repo.save(order)
            self._event(live_order_id, candidate.signal_id, "PLACED",
                        {"entry_order_id": bn_order_id, "qty": str(qty), "leverage": leverage,
                         "sl_order_id": sl_order_id, "tp_order_id": tp_order_id, "otoco": otoco_ok})
            log.info("[Live] placed %s %s %s qty=%s lev=%sx otoco=%s",
                     live_order_id, candidate.symbol, side, qty, leverage, otoco_ok)
            return PlaceResult(ok=True, live_order_id=live_order_id)

        except BinanceFuturesError as exc:
            reason = f"Binance {exc.code}: {exc.msg}"
            log.warning("[Live] place failed %s: %s", candidate.symbol, reason)
            self._save_rejected(live_order_id, candidate, now, reason)
            return PlaceResult(ok=False, reason=reason, live_order_id=live_order_id)
        except Exception as exc:
            reason = str(exc)[:80]
            log.exception("[Live] unexpected error placing %s", candidate.symbol)
            self._save_rejected(live_order_id, candidate, now, reason)
            return PlaceResult(ok=False, reason=reason, live_order_id=live_order_id)

    # ── Sync ──────────────────────────────────────────────────────────────────

    def sync_all(self) -> int:
        """Check pending/filled orders, attach SL/TP after entry fill. Returns updated count."""
        updated = 0
        for order in self._repo.load_by_status("PENDING"):
            try:
                if self._sync_pending(order):
                    updated += 1
            except Exception:
                log.exception("[Live] sync_pending failed for %s", order.live_order_id)

        for order in self._repo.load_by_status("FILLED"):
            try:
                if self._sync_filled(order):
                    updated += 1
            except Exception:
                log.exception("[Live] sync_filled failed for %s", order.live_order_id)

        return updated

    def _sync_pending(self, order: LiveOrder) -> bool:
        if not order.entry_order_id:
            return False
        bn = self._client.get_order(order.symbol, order.entry_order_id)
        status = bn.get("status", "")
        if status == "FILLED":
            qty = Decimal(str(bn.get("executedQty", order.quantity)))
            avg_price = Decimal(str(bn.get("avgPrice", order.entry)))

            # OTOCO order: SL/TP already placed at order time — just update status
            if order.sl_order_id and order.tp_order_id:
                self._repo.update_status(order.live_order_id, "FILLED")
                self._event(order.live_order_id, order.signal_id, "FILLED",
                            {"avg_price": str(avg_price), "qty": str(qty)})
                self._notify(
                    f"[Live] ✅ 入场成交 (OTOCO)\n"
                    f"━━━━━━━━━━━━━\n"
                    f"{order.symbol}  {order.direction}\n"
                    f"成交价  {avg_price}\n"
                    f"数量    {qty}\n"
                    f"SL      {order.sl}  ✅\n"
                    f"TP      {order.tp}  ✅"
                )
                log.info("[Live] OTOCO entry filled %s, sl=%s tp=%s (pre-attached)",
                         order.live_order_id, order.sl_order_id, order.tp_order_id)
                return True

            # Plain LIMIT order: attach SL/TP now
            sl_id = self._attach_sl_with_retry(order, qty)
            tp_id = self._attach_tp_with_retry(order, qty)
            self._repo.update_status(order.live_order_id, "FILLED", sl_order_id=sl_id, tp_order_id=tp_id)
            self._event(order.live_order_id, order.signal_id, "FILLED",
                        {"avg_price": str(avg_price), "qty": str(qty)})
            sl_status = sl_id or "❌失败"
            tp_status = tp_id or "❌失败"
            self._notify(
                f"[Live] ✅ 入场成交\n"
                f"━━━━━━━━━━━━━\n"
                f"{order.symbol}  {order.direction}\n"
                f"成交价  {avg_price}\n"
                f"数量    {qty}\n"
                f"SL      {order.sl}  (单号: {sl_status})\n"
                f"TP      {order.tp}  (单号: {tp_status})"
            )
            if not sl_id:
                log.error("[Live] SL attachment FAILED after 3 retries for %s %s",
                          order.live_order_id, order.symbol)
            log.info("[Live] entry filled %s, sl=%s tp=%s", order.live_order_id, sl_id, tp_id)
            return True
        elif status in ("CANCELED", "EXPIRED", "REJECTED"):
            self._repo.update_status(order.live_order_id, "CANCELED")
            self._event(order.live_order_id, order.signal_id, "ENTRY_CANCELED", {"bn_status": status})
            return True
        return False

    def _sync_filled(self, order: LiveOrder) -> bool:
        updated = False
        needs_sl = order.sl_order_id is None
        needs_tp = order.tp_order_id is None

        if needs_sl or needs_tp:
            # Query actual position from Binance for real qty (not stored qty)
            # In hedge mode match both symbol AND positionSide to avoid cross-side false positives
            real_qty: Decimal | None = None
            get_pos_failed = False
            try:
                positions = self._client.get_positions()
                for p in positions:
                    sym_match  = p.get("symbol") == order.symbol
                    side_match = p.get("positionSide", "BOTH") == order.direction
                    if sym_match and side_match:
                        amt = Decimal(str(p.get("positionAmt", "0")))
                        real_qty = abs(amt) if amt != 0 else None
                        break
            except Exception as exc:
                log.warning("[Live] get_positions failed during naked check %s: %s",
                            order.live_order_id, exc)
                get_pos_failed = True

            # If position no longer exists on Binance, it was closed externally — mark CLOSED
            # (skip this conclusion if the API call itself failed, to avoid false closures)
            if real_qty is None and not get_pos_failed:
                log.info("[Live] %s %s has no open position on Binance — marking CLOSED",
                         order.live_order_id, order.symbol)
                self._repo.update_status(order.live_order_id, "CLOSED")
                self._event(order.live_order_id, order.signal_id, "CLOSED_EXTERNAL",
                            {"reason": "no_open_position"})
                return True

            if real_qty is None:
                # API failed; fall back to stored quantity so SL/TP attachment can still be attempted
                real_qty = Decimal(order.quantity)

            qty = real_qty

            if needs_sl:
                log.warning("[Live] NAKED POSITION detected: %s %s", order.live_order_id, order.symbol)
                self._notify(
                    f"[Live] ⚠️ 裸仓警告！无止损单！\n"
                    f"{order.symbol}  {order.direction}\n"
                    f"live_order_id: {order.live_order_id}\n"
                    f"正在自动补挂止损..."
                )
                sl_id = self._attach_sl_with_retry(order, qty)
                if sl_id:
                    self._repo.update_status(order.live_order_id, "FILLED", sl_order_id=sl_id)
                    self._event(order.live_order_id, order.signal_id, "NAKED_SL_ATTACHED",
                                {"sl_id": sl_id, "qty": str(qty)})
                    self._notify(
                        f"[Live] ✅ 止损已补挂\n"
                        f"{order.symbol}  SL @ {order.sl}\n"
                        f"单号: {sl_id}"
                    )
                    updated = True
                else:
                    self._notify(
                        f"[Live] 🚨 止损自动补挂失败（该币种不支持独立止损单）\n"
                        f"{order.symbol}  {order.direction}  SL @ {order.sl}\n"
                        f"live_order_id: {order.live_order_id}\n"
                        f"👉 请在 Binance App → 持仓 → 该仓位右侧 TP/SL 按钮手动设置\n"
                        f"    止损: {order.sl}  止盈: {order.tp}"
                    )

            if needs_tp:
                tp_id = self._attach_tp_with_retry(order, qty)
                if tp_id:
                    self._repo.update_status(order.live_order_id, "FILLED", tp_order_id=tp_id)
                    self._event(order.live_order_id, order.signal_id, "NAKED_TP_ATTACHED",
                                {"tp_id": tp_id, "qty": str(qty)})
                    updated = True

            return updated

        closed_by: str | None = None

        if order.sl_order_id:
            try:
                sl = self._client.get_order(order.symbol, order.sl_order_id)
                if sl.get("status") == "FILLED":
                    closed_by = "CLOSED_SL"
            except Exception:
                pass

        if closed_by is None and order.tp_order_id:
            try:
                tp = self._client.get_order(order.symbol, order.tp_order_id)
                if tp.get("status") == "FILLED":
                    closed_by = "CLOSED_TP"
            except Exception:
                pass

        if closed_by:
            self._repo.update_status(order.live_order_id, closed_by)
            self._event(order.live_order_id, order.signal_id, closed_by, {})
            emoji = "✅" if closed_by == "CLOSED_TP" else "❌"
            self._notify(
                f"[Live] {emoji} 结算 {closed_by}\n"
                f"━━━━━━━━━━━━━\n"
                f"{order.symbol}  {order.direction}"
            )
            log.info("[Live] order closed %s via %s", order.live_order_id, closed_by)
            return True
        return False

    # ── SL / TP ───────────────────────────────────────────────────────────────

    _ATTACH_RETRIES = 3
    _ATTACH_RETRY_DELAY = 2.0  # seconds between retries

    def _attach_sl_with_retry(self, order: LiveOrder, qty: Decimal) -> str | None:
        for attempt in range(1, self._ATTACH_RETRIES + 1):
            result = self._attach_sl(order, qty)
            if result:
                return result
            if attempt < self._ATTACH_RETRIES:
                log.warning("[Live] SL retry %d/%d for %s", attempt, self._ATTACH_RETRIES,
                            order.live_order_id)
                time.sleep(self._ATTACH_RETRY_DELAY)
        return None

    def _attach_tp_with_retry(self, order: LiveOrder, qty: Decimal) -> str | None:
        for attempt in range(1, self._ATTACH_RETRIES + 1):
            result = self._attach_tp(order, qty)
            if result:
                return result
            if attempt < self._ATTACH_RETRIES:
                log.warning("[Live] TP retry %d/%d for %s", attempt, self._ATTACH_RETRIES,
                            order.live_order_id)
                time.sleep(self._ATTACH_RETRY_DELAY)
        return None

    def _attach_sl(self, order: LiveOrder, qty: Decimal) -> str | None:
        try:
            sl_price = Decimal(order.sl)
            close_side = "BUY" if order.direction == "SHORT" else "SELL"
            sl_price_r = self._client.round_price(order.symbol, sl_price)
            resp = self._client.place_stop_market(
                order.symbol, close_side, sl_price_r, qty, position_side=order.direction
            )
            sl_id = str(resp.get("orderId", ""))
            self._event(order.live_order_id, order.signal_id, "SL_PLACED",
                        {"sl_order_id": sl_id, "stop_price": str(sl_price_r)})
            return sl_id
        except BinanceFuturesError as exc:
            log.warning("[Live] attach_sl failed %s: %s", order.live_order_id, exc)
            self._event(order.live_order_id, order.signal_id, "SL_FAILED", {"error": str(exc)})
            return None

    def _attach_tp(self, order: LiveOrder, qty: Decimal) -> str | None:
        try:
            tp_price = Decimal(order.tp)
            close_side = "BUY" if order.direction == "SHORT" else "SELL"
            tp_price_r = self._client.round_price(order.symbol, tp_price)
            resp = self._client.place_take_profit_market(
                order.symbol, close_side, tp_price_r, qty, position_side=order.direction
            )
            tp_id = str(resp.get("orderId", ""))
            self._event(order.live_order_id, order.signal_id, "TP_PLACED",
                        {"tp_order_id": tp_id, "stop_price": str(tp_price_r)})
            return tp_id
        except BinanceFuturesError as exc:
            log.warning("[Live] attach_tp failed %s: %s", order.live_order_id, exc)
            self._event(order.live_order_id, order.signal_id, "TP_FAILED", {"error": str(exc)})
            return None

    # ── Risk checks ───────────────────────────────────────────────────────────

    def _risk_check(self, candidate: V3Candidate) -> str | None:
        entry = Decimal(candidate.entry)
        sl    = Decimal(candidate.sl)

        if not candidate.entry or not candidate.sl or not candidate.tp1:
            return "缺少 entry/SL/TP"

        sl_pct = abs(entry - sl) / entry * 100
        if sl_pct > _MAX_SL_PCT:
            return f"SL距离{sl_pct:.1f}%>10%"

        try:
            open_orders = self._client.get_open_orders()
            if len(open_orders) >= self._max_pend:
                return f"挂单数{len(open_orders)}≥{self._max_pend}"
        except Exception as exc:
            log.warning("[Live] open_orders check failed: %s", exc)

        try:
            positions = self._client.get_positions()
            if len(positions) >= self._max_pos:
                return f"持仓数{len(positions)}≥{self._max_pos}"
            pos_symbols = {p.get("symbol") for p in positions}
            if candidate.symbol in pos_symbols:
                return f"{candidate.symbol}已有持仓"
        except Exception as exc:
            log.warning("[Live] position check failed: %s", exc)

        active_symbols = self._repo.load_active_symbols()
        if candidate.symbol in active_symbols:
            return f"{candidate.symbol}已有挂单"

        try:
            balance = self._client.get_balance()
            leverage = self._choose_leverage(candidate.symbol)
            required_margin = self._notional / Decimal(leverage)
            if balance < required_margin:
                return f"余额{balance:.0f}U不足(需{required_margin:.0f}U)"
        except Exception as exc:
            log.warning("[Live] balance check failed: %s", exc)

        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _choose_leverage(self, symbol: str) -> int:
        try:
            max_lev = self._client.get_max_leverage(symbol)
            return min(10, max_lev)
        except Exception:
            return 10

    def _calc_quantity(self, symbol: str, entry: Decimal, leverage: int) -> Decimal:
        raw_qty = self._notional / entry
        return self._client.round_quantity(symbol, raw_qty)

    def _save_rejected(
        self, live_order_id: str, candidate: V3Candidate, now: str, reason: str
    ) -> None:
        order = LiveOrder(
            live_order_id  = live_order_id,
            signal_id      = candidate.signal_id,
            symbol         = candidate.symbol,
            side           = "BUY" if candidate.direction == "LONG" else "SELL",
            direction      = candidate.direction,
            entry          = candidate.entry,
            sl             = candidate.sl,
            tp             = candidate.tp1,
            notional       = str(self._notional),
            leverage       = 0,
            quantity       = "0",
            status         = "REJECTED",
            entry_order_id = None,
            sl_order_id    = None,
            tp_order_id    = None,
            created_at     = now,
            updated_at     = now,
            reject_reason  = reason,
        )
        try:
            self._repo.save(order)
            self._event(live_order_id, candidate.signal_id, "REJECTED", {"reason": reason})
        except Exception:
            log.exception("[Live] failed to save rejected order")

    def _event(self, live_order_id: str, signal_id: str, event_type: str, details: dict) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            self._repo.append_event(LiveEvent(
                event_id      = make_live_event_id(),
                live_order_id = live_order_id,
                signal_id     = signal_id,
                event_type    = event_type,
                details_json  = json.dumps(details),
                created_at    = now,
            ))
        except Exception:
            log.exception("[Live] failed to append event %s", event_type)

    def _notify(self, msg: str) -> None:
        if self._notifier:
            try:
                self._notifier.send(msg)
            except Exception:
                log.exception("[Live] notification failed")

    # ── Status / CLI helpers ──────────────────────────────────────────────────

    def get_account_status(self) -> dict:
        """Return account summary dict for CLI / report."""
        try:
            account = self._client.get_account()
            balance = Decimal("0")
            avail   = Decimal("0")
            unrealised = Decimal("0")
            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    balance    = Decimal(str(asset.get("walletBalance", "0")))
                    avail      = Decimal(str(asset.get("availableBalance", "0")))
                    unrealised = Decimal(str(asset.get("unrealizedProfit", "0")))
                    break
            positions = self._client.get_positions()
            open_orders = self._client.get_open_orders()
            today = self._client.get_today_trades_summary()
            return {
                "balance":        balance,
                "available":      avail,
                "unrealized_pnl": unrealised,
                "realized_pnl":   today.get("realized_pnl", Decimal("0")),
                "positions":      positions,
                "open_orders":    open_orders,
            }
        except Exception as exc:
            return {"error": str(exc)}
