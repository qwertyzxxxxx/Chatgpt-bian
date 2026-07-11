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
    LiveOrderStatus,
    PlaceResult,
    make_live_event_id,
    make_live_order_id,
)
from binance_ai_trader.v3.live.order_manager import LiveOrderManager
from binance_ai_trader.v3.live.repository import LiveOrderRepository

log = logging.getLogger(__name__)


class LiveMirrorEngine:
    def __init__(
        self,
        client: BinanceFuturesClient,
        repo: LiveOrderRepository,
        notifier: TelegramNotifier | None = None,
        notional_usdt: Decimal = Decimal("1000"),
        max_pending: int = 10,
        max_positions: int = 5,
        strategy_id: str = "hotlist_momentum_v3",
        tag: str | None = None,
    ) -> None:
        self._client   = client
        self._repo     = repo
        self._notifier = notifier
        self._notional = notional_usdt
        self._max_pend = max_pending
        self._max_pos  = max_positions
        self._strategy_id = strategy_id
        # Short label used to prefix Telegram messages (e.g. "[V3]"/"[V66]") so
        # the two strategies' live notifications are never confused with each
        # other. Defaults to a readable form of strategy_id if not given.
        self._tag = tag or strategy_id
        self._order_manager = LiveOrderManager()

    def is_enabled(self) -> bool:
        """Live trading requires BOTH the global kill switch (env var) AND
        this strategy's own DB-backed toggle (set via /livemode in Telegram).

        The env var acts as an emergency master switch that always wins —
        flipping it off disables live trading for every strategy at once,
        regardless of what's stored in the database.
        """
        if os.environ.get("LIVE_TRADING_ENABLED", "").lower() != "true":
            return False
        try:
            from binance_ai_trader.v3.settings.repository import V3RuntimeSettingsRepository

            live_enabled, _ = V3RuntimeSettingsRepository().resolve_live(self._strategy_id)
            return live_enabled
        except Exception:
            log.exception("[Live] failed to resolve live_enabled for %s — defaulting to disabled",
                          self._strategy_id)
            return False

    def effective_notional(self) -> Decimal:
        """Current live position size (USDT), honoring any DB override."""
        try:
            from binance_ai_trader.v3.settings.repository import V3RuntimeSettingsRepository

            _, notional = V3RuntimeSettingsRepository().resolve_live(self._strategy_id)
            return notional
        except Exception:
            log.exception("[Live] failed to resolve notional for %s — using constructor default",
                          self._strategy_id)
            return self._notional

    # ── Place ──────────────────────────────────────────────────────────────────

    def try_place(self, candidate: V3Candidate) -> PlaceResult:
        if not self.is_enabled():
            return PlaceResult(ok=False, reason="LIVE_TRADING_ENABLED!=true")

        now = datetime.now(UTC).isoformat(timespec="seconds")
        live_order_id = make_live_order_id()

        try:
            if not candidate.entry or not candidate.sl or not candidate.tp1:
                reason = "缺少 entry/SL/TP"
                self._save_status_order(live_order_id, candidate, now, "REJECTED", reason)
                return PlaceResult(ok=False, reason=reason, live_order_id=live_order_id)

            entry = Decimal(candidate.entry)

            conflict_reason = self._handle_conflicts(candidate, live_order_id, now, entry)
            if conflict_reason is not None:
                return PlaceResult(ok=False, reason=conflict_reason, live_order_id=live_order_id)

            reason = self._risk_check(candidate)
            if reason:
                self._save_rejected(live_order_id, candidate, now, reason)
                return PlaceResult(ok=False, reason=reason, live_order_id=live_order_id)

            sl    = Decimal(candidate.sl)
            tp    = Decimal(candidate.tp2 if candidate.tp2 else candidate.tp1)
            side  = "BUY" if candidate.direction == "LONG" else "SELL"
            pos_side = candidate.direction  # "LONG" or "SHORT"

            leverage = self._choose_leverage(candidate.symbol)
            try:
                self._client.set_leverage(candidate.symbol, leverage)
            except BinanceFuturesError as exc:
                log.warning("[Live] set_leverage failed for %s: %s", candidate.symbol, exc)

            notional = self.effective_notional()
            qty = self._calc_quantity(candidate.symbol, entry, leverage, notional)
            if qty <= 0:
                reason = "数量计算结果≤0"
                self._save_rejected(live_order_id, candidate, now, reason)
                return PlaceResult(ok=False, reason=reason, live_order_id=live_order_id)

            entry_r = self._client.round_price(candidate.symbol, entry)
            sl_r    = self._client.round_price(candidate.symbol, sl)
            tp_r    = self._client.round_price(candidate.symbol, tp)

            # Try batch (entry + SL + TP in one call → minimal naked-position window)
            bn_order_id: str = ""
            sl_order_id: str | None = None
            tp_order_id: str | None = None
            batch_ok = False
            try:
                batch = self._client.place_entry_with_sltp(
                    candidate.symbol, side, entry_r, qty, tp_r, sl_r, position_side=pos_side
                )
                bn_order_id = batch["entry_order_id"]
                sl_order_id = batch["sl_order_id"] or None
                tp_order_id = batch["tp_order_id"] or None
                batch_ok = True
                log.info("[Live] batch placed %s %s %s entry=%s sl=%s tp=%s sl_id=%s tp_id=%s",
                         live_order_id, candidate.symbol, side, entry_r, sl_r, tp_r,
                         sl_order_id, tp_order_id)
            except BinanceFuturesError as exc:
                log.warning("[Live] batch failed for %s (%s), falling back to plain LIMIT: %s",
                            candidate.symbol, exc.code, exc.msg)

            if not batch_ok:
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
                notional       = str(notional),
                leverage       = leverage,
                quantity       = str(qty),
                status         = "PENDING",
                entry_order_id = bn_order_id,
                sl_order_id    = sl_order_id,
                tp_order_id    = tp_order_id,
                created_at     = now,
                updated_at     = now,
                reject_reason  = None,
                strategy_id    = self._strategy_id,
            )
            self._repo.save(order)
            self._event(live_order_id, candidate.signal_id, "PLACED",
                        {"entry_order_id": bn_order_id, "qty": str(qty), "leverage": leverage,
                         "sl_order_id": sl_order_id, "tp_order_id": tp_order_id, "otoco": batch_ok})
            log.info("[Live] placed %s %s %s qty=%s lev=%sx otoco=%s",
                     live_order_id, candidate.symbol, side, qty, leverage, batch_ok)
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
        """Check pending/filled orders, attach SL/TP after entry fill. Returns updated count.

        Scoped to this engine's own strategy_id — the V3 and V66 engines each
        sync only their own orders, so one strategy's sync loop can never
        touch or reconcile the other's live_orders rows.
        """
        updated = 0
        for order in self._repo.load_by_status("PENDING", strategy_id=self._strategy_id):
            try:
                if self._sync_pending(order):
                    updated += 1
            except Exception:
                log.exception("[Live] sync_pending failed for %s", order.live_order_id)

        for order in self._repo.load_by_status("FILLED", strategy_id=self._strategy_id):
            try:
                if self._sync_filled(order):
                    updated += 1
            except Exception:
                log.exception("[Live] sync_filled failed for %s", order.live_order_id)

        try:
            updated += self._expire_stale_pending()
        except Exception:
            log.exception("[Live] expire_stale_pending failed")

        return updated

    # ── Conflict management (Part B) ────────────────────────────────────────────

    def _handle_conflicts(
        self, candidate: V3Candidate, live_order_id: str, now: str, entry: Decimal
    ) -> str | None:
        """Resolve same-symbol conflicts before placing a new order.

        Returns None if the caller should proceed to place the new order
        (either no conflict, or the conflicting order was just replaced).
        Returns a reason string if placement should be skipped (terminal).
        """
        # Scoped to this strategy: V3 and V66 resolve conflicts independently,
        # so a V66 signal can never see (or replace/cancel) a V3 order on the
        # same symbol, and vice versa.
        pending = self._repo.load_pending_by_symbol(candidate.symbol, strategy_id=self._strategy_id)
        filled = self._repo.load_filled_by_symbol(candidate.symbol, strategy_id=self._strategy_id)
        decision = self._order_manager.resolve(candidate.symbol, candidate.direction, entry, pending, filled)

        if decision.action == "PLACE":
            return None

        old = decision.conflicting_order

        if decision.action == "REPLACE":
            self._cancel_binance_order(old)
            self._repo.update_status(old.live_order_id, LiveOrderStatus.REPLACED, reject_reason=decision.reason)
            self._conflict_event(old, candidate, "REPLACED", decision.reason)
            self._notify(
                f"[Live] 🔁 替换旧挂单\n{candidate.symbol} {candidate.direction}\n"
                f"旧入场:{old.entry} → 新入场:{candidate.entry}\n{decision.reason}"
            )
            return None  # fall through: place the new order

        status_map = {
            "IGNORE_DUPLICATE": LiveOrderStatus.IGNORED_DUPLICATE,
            "IGNORE_WORSE_ENTRY": LiveOrderStatus.IGNORED_WORSE_ENTRY,
            "CANCEL_CONFLICT": LiveOrderStatus.DIRECTION_CONFLICT,
            "POSITION_SAME_SIDE": LiveOrderStatus.POSITION_EXISTS_SAME_SIDE,
            "POSITION_OPPOSITE_SIDE": LiveOrderStatus.POSITION_EXISTS_OPPOSITE_SIDE,
        }
        status = status_map[decision.action]

        if decision.action == "CANCEL_CONFLICT" and old is not None:
            self._cancel_binance_order(old)
            self._repo.update_status(old.live_order_id, LiveOrderStatus.DIRECTION_CONFLICT,
                                      reject_reason=decision.reason)

        self._save_status_order(live_order_id, candidate, now, status, decision.reason)
        self._conflict_event(old, candidate, decision.action, decision.reason)

        if decision.action in ("CANCEL_CONFLICT", "POSITION_OPPOSITE_SIDE"):
            self._notify(f"[Live] ⚠️ {decision.action}\n{candidate.symbol} {candidate.direction}\n{decision.reason}")

        return decision.reason

    def _cancel_remaining_algo_orders(
        self, order: LiveOrder, skip_sl: bool = False, skip_tp: bool = False
    ) -> None:
        """Cancel whichever SL/TP algo order(s) are still open on Binance for
        a position that is now flat (closed via the other leg, or externally).

        Without this, the un-triggered leg keeps sitting on Binance as a live
        conditional order with no position behind it — the exact "挂单没有撤回"
        (pending order never cancelled) bug reported by the user.
        """
        pairs = []
        if not skip_sl and order.sl_order_id:
            pairs.append(order.sl_order_id)
        if not skip_tp and order.tp_order_id:
            pairs.append(order.tp_order_id)
        for algo_id in pairs:
            try:
                self._client.cancel_algo_order(order.symbol, algo_id)
                log.info("[Live] canceled dangling algo order %s for %s (%s)",
                         algo_id, order.live_order_id, order.symbol)
            except BinanceFuturesError as exc:
                # -2011/"Unknown order" just means it was already filled/canceled — fine.
                log.info("[Live] cancel dangling algo order %s failed (likely already gone): %s",
                         algo_id, exc)
            except Exception:
                log.exception("[Live] unexpected error canceling dangling algo order %s", algo_id)

    def _cancel_binance_order(self, old: LiveOrder | None) -> None:
        if old is None:
            return
        if old.entry_order_id:
            try:
                self._client.cancel_order(old.symbol, old.entry_order_id)
            except BinanceFuturesError as exc:
                log.warning("[Live] cancel old order failed %s: %s", old.live_order_id, exc)
            except Exception:
                log.exception("[Live] unexpected error canceling old order %s", old.live_order_id)
        # SL/TP are separate Algo orders (see client.place_entry_with_sltp) — cancel
        # those too so they don't linger as dangling conditional orders.
        for algo_id in (old.sl_order_id, old.tp_order_id):
            if not algo_id:
                continue
            try:
                self._client.cancel_algo_order(old.symbol, algo_id)
            except BinanceFuturesError as exc:
                log.warning("[Live] cancel old algo order failed %s: %s", old.live_order_id, exc)
            except Exception:
                log.exception("[Live] unexpected error canceling old algo order %s", old.live_order_id)

    def _conflict_event(
        self, old: LiveOrder | None, candidate: V3Candidate, action: str, reason: str
    ) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        new_side = "BUY" if candidate.direction == "LONG" else "SELL"
        try:
            self._repo.append_event(LiveEvent(
                event_id=make_live_event_id(),
                live_order_id=old.live_order_id if old else "",
                signal_id=candidate.signal_id,
                event_type=action,
                details_json=json.dumps({"reason": reason}),
                created_at=now,
                old_signal_id=old.signal_id if old else None,
                new_signal_id=candidate.signal_id,
                symbol=candidate.symbol,
                old_side=old.side if old else None,
                new_side=new_side,
                old_entry=old.entry if old else None,
                new_entry=candidate.entry,
                action=action,
                reason=reason,
            ))
        except Exception:
            log.exception("[Live] failed to append conflict event %s", action)

    # ── Pending-order expiry sweep ───────────────────────────────────────────────

    _PENDING_EXPIRY_HOURS = 24
    _PENDING_PRICE_DRIFT_PCT = Decimal("5")

    def _expire_stale_pending(self) -> int:
        count = 0
        now = datetime.now(UTC)
        for order in self._repo.load_by_status("PENDING", strategy_id=self._strategy_id):
            try:
                reason = self._stale_reason(order, now)
            except Exception:
                log.exception("[Live] stale check failed for %s", order.live_order_id)
                continue
            if reason is None:
                continue
            self._cancel_binance_order(order)
            self._repo.update_status(order.live_order_id, LiveOrderStatus.CANCELED_EXPIRED, reject_reason=reason)
            self._event(order.live_order_id, order.signal_id, "CANCELED_EXPIRED", {"reason": reason})
            self._notify(f"[Live] ⏰ 挂单自动撤销\n{order.symbol} {order.direction}\n{reason}")
            count += 1
        return count

    def _stale_reason(self, order: LiveOrder, now: datetime) -> str | None:
        try:
            created = datetime.fromisoformat(order.created_at.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
        except Exception:
            created = now

        age_hours = (now - created).total_seconds() / 3600
        if age_hours >= self._PENDING_EXPIRY_HOURS:
            return f"挂单超过{self._PENDING_EXPIRY_HOURS}小时未成交，自动撤销"

        try:
            current_price = self._client.get_ticker_price(order.symbol)
            entry = Decimal(order.entry)
            if entry > 0:
                drift = abs(Decimal(str(current_price)) - entry) / entry * 100
                if drift > self._PENDING_PRICE_DRIFT_PCT:
                    return f"现价偏离入场价{drift:.1f}%>{self._PENDING_PRICE_DRIFT_PCT}%，行情已远离，自动撤销"
        except Exception as exc:
            log.debug("[Live] price drift check unavailable for %s: %s", order.symbol, exc)

        return None

    def _sync_pending(self, order: LiveOrder) -> bool:
        if not order.entry_order_id:
            return False
        bn = self._client.get_order(order.symbol, order.entry_order_id)
        status = bn.get("status", "")
        if status == "FILLED":
            qty = Decimal(str(bn.get("executedQty", order.quantity)))
            avg_price = Decimal(str(bn.get("avgPrice", order.entry)))

            # OTOCO order: SL/TP already placed at order time — just update status.
            # Both legs present means the batch call fully succeeded.
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

            # Only attach whichever leg is actually missing. A partially-successful
            # batch call (e.g. SL placed but TP algo order rejected) already has one
            # leg's order id populated on `order` — re-attaching it here would create
            # a duplicate order on Binance sitting alongside the original.
            sl_id = order.sl_order_id or self._attach_sl_with_retry(order, qty)
            tp_id = order.tp_order_id or self._attach_tp_with_retry(order, qty)
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
                self._notify(
                    f"[Live] 🚨 止损挂单失败（重试3次后仍失败）\n"
                    f"{order.symbol}  {order.direction}  SL @ {order.sl}\n"
                    f"live_order_id: {order.live_order_id}\n"
                    f"👉 请立即在 Binance App 手动设置止损"
                )
            if not tp_id:
                # TP failures used to be logged only (no Telegram alert), which is
                # exactly how the "漏挂止盈" issue went unnoticed — SL failures paged
                # loudly while TP failures sat silently in the log. Alert on both now.
                log.error("[Live] TP attachment FAILED after 3 retries for %s %s",
                          order.live_order_id, order.symbol)
                self._notify(
                    f"[Live] 🚨 止盈挂单失败（重试3次后仍失败）\n"
                    f"{order.symbol}  {order.direction}  TP @ {order.tp}\n"
                    f"live_order_id: {order.live_order_id}\n"
                    f"👉 请立即在 Binance App 手动设置止盈"
                )
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
                # Neither SL nor TP algo order exists (needs_sl/needs_tp both true
                # got us here) yet the position is already gone — this can only be
                # an external/manual close (or an entry that was flattened before
                # either leg attached). Tag it distinctly so performance stats can
                # exclude manual intervention from win-rate calculations.
                log.info("[Live] %s %s has no open position on Binance — marking MANUAL_CLOSED",
                         order.live_order_id, order.symbol)
                self._cancel_remaining_algo_orders(order)
                self._repo.update_status(order.live_order_id, "MANUAL_CLOSED")
                self._event(order.live_order_id, order.signal_id, "CLOSED_EXTERNAL",
                            {"reason": "no_open_position"})
                self._notify(
                    f"[Live] ℹ️ 检测到手动平仓（无止盈/止损触发记录）\n"
                    f"{order.symbol}  {order.direction}\n"
                    f"live_order_id: {order.live_order_id}\n"
                    f"该笔不计入策略胜率统计"
                )
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
                if needs_sl:
                    self._notify(
                        f"[Live] ⚠️ 裸仓警告！无止盈单！\n"
                        f"{order.symbol}  {order.direction}\n"
                        f"live_order_id: {order.live_order_id}\n"
                        f"正在自动补挂止盈..."
                    )
                tp_id = self._attach_tp_with_retry(order, qty)
                if tp_id:
                    self._repo.update_status(order.live_order_id, "FILLED", tp_order_id=tp_id)
                    self._event(order.live_order_id, order.signal_id, "NAKED_TP_ATTACHED",
                                {"tp_id": tp_id, "qty": str(qty)})
                    self._notify(
                        f"[Live] ✅ 止盈已补挂\n"
                        f"{order.symbol}  TP @ {order.tp}\n"
                        f"单号: {tp_id}"
                    )
                    updated = True
                else:
                    # Previously this failure was silent (log only) — that's exactly
                    # how a missing TP could go unnoticed for hours. Alert loudly,
                    # matching the SL failure treatment above.
                    self._notify(
                        f"[Live] 🚨 止盈自动补挂失败\n"
                        f"{order.symbol}  {order.direction}  TP @ {order.tp}\n"
                        f"live_order_id: {order.live_order_id}\n"
                        f"👉 请在 Binance App → 持仓 → 该仓位右侧 TP/SL 按钮手动设置\n"
                        f"    止损: {order.sl}  止盈: {order.tp}"
                    )

            return updated

        closed_by: str | None = None

        if order.sl_order_id:
            sl_id_lost = False
            try:
                sl = self._client.get_algo_order(order.symbol, order.sl_order_id)
                if sl.get("status") == "FILLED":
                    closed_by = "CLOSED_SL"
                elif sl.get("status") in ("CANCELED", "EXPIRED", "REJECTED"):
                    # The stored algoOrderId is stale — e.g. it was manually
                    # cancelled/replaced on Binance's side. The old flow could
                    # never detect this and would leave the position naked
                    # forever. Clear it so the naked-position path re-attaches.
                    sl_id_lost = True
            except BinanceFuturesError:
                # -2013/"Order does not exist" — the algoOrderId no longer
                # resolves on Binance at all.
                sl_id_lost = True
            except Exception:
                pass

            if sl_id_lost:
                log.warning("[Live] SL algo order %s for %s no longer valid on Binance — "
                            "clearing and re-attaching", order.sl_order_id, order.live_order_id)
                self._repo.clear_algo_ids(order.live_order_id, clear_sl=True, clear_tp=False)
                self._notify(
                    f"[Live] ⚠️ 止损单在币安端已失效（可能被手动撤销）\n"
                    f"{order.symbol}  {order.direction}\n"
                    f"live_order_id: {order.live_order_id}\n"
                    f"正在自动重新补挂止损..."
                )
                return updated

        if closed_by is None and order.tp_order_id:
            tp_id_lost = False
            try:
                tp = self._client.get_algo_order(order.symbol, order.tp_order_id)
                if tp.get("status") == "FILLED":
                    closed_by = "CLOSED_TP"
                elif tp.get("status") in ("CANCELED", "EXPIRED", "REJECTED"):
                    tp_id_lost = True
            except BinanceFuturesError:
                tp_id_lost = True
            except Exception:
                pass

            if tp_id_lost:
                log.warning("[Live] TP algo order %s for %s no longer valid on Binance — "
                            "clearing and re-attaching", order.tp_order_id, order.live_order_id)
                self._repo.clear_algo_ids(order.live_order_id, clear_sl=False, clear_tp=True)
                self._notify(
                    f"[Live] ⚠️ 止盈单在币安端已失效（可能被手动撤销）\n"
                    f"{order.symbol}  {order.direction}\n"
                    f"live_order_id: {order.live_order_id}\n"
                    f"正在自动重新补挂止盈..."
                )
                return updated

        if closed_by:
            # The triggered leg (SL or TP) has already been consumed by Binance —
            # cancel the OTHER leg so it doesn't linger as a dangling algo order
            # once the position is flat.
            self._cancel_remaining_algo_orders(order, skip_sl=(closed_by == "CLOSED_SL"),
                                                skip_tp=(closed_by == "CLOSED_TP"))
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
            sl_id = str(resp.get("algoId", resp.get("orderId", "")))
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
            tp_id = str(resp.get("algoId", resp.get("orderId", "")))
            self._event(order.live_order_id, order.signal_id, "TP_PLACED",
                        {"tp_order_id": tp_id, "stop_price": str(tp_price_r)})
            return tp_id
        except BinanceFuturesError as exc:
            log.warning("[Live] attach_tp failed %s: %s", order.live_order_id, exc)
            self._event(order.live_order_id, order.signal_id, "TP_FAILED", {"error": str(exc)})
            return None

    # ── Risk checks ───────────────────────────────────────────────────────────

    _LIVE_MAX_STOP_PCT = Decimal("8")  # live orders only: SL must be ≤8% from entry

    def _risk_check(self, candidate: V3Candidate) -> str | None:
        entry = Decimal(candidate.entry)
        sl    = Decimal(candidate.sl)

        if not candidate.entry or not candidate.sl or not candidate.tp1:
            return "缺少 entry/SL/TP"

        sl_pct = abs(entry - sl) / entry * 100
        if sl_pct > self._LIVE_MAX_STOP_PCT:
            return f"止损距离{sl_pct:.1f}%>{self._LIVE_MAX_STOP_PCT}%(实盘限制)"

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
        except Exception as exc:
            log.warning("[Live] position check failed: %s", exc)

        # NOTE: same-symbol pending/position conflicts are now handled by
        # `_handle_conflicts()` / LiveOrderManager before this risk check runs
        # (see try_place) — no blanket same-symbol rejection here anymore.

        try:
            balance = self._client.get_balance()
            leverage = self._choose_leverage(candidate.symbol)
            required_margin = self.effective_notional() / Decimal(leverage)
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

    def _calc_quantity(self, symbol: str, entry: Decimal, leverage: int, notional: Decimal | None = None) -> Decimal:
        raw_qty = (notional if notional is not None else self._notional) / entry
        return self._client.round_quantity(symbol, raw_qty)

    def _save_rejected(
        self, live_order_id: str, candidate: V3Candidate, now: str, reason: str
    ) -> None:
        self._save_status_order(live_order_id, candidate, now, "REJECTED", reason)

    def _save_status_order(
        self, live_order_id: str, candidate: V3Candidate, now: str, status: str, reason: str
    ) -> None:
        """Persist a terminal (non-placed) live_order row — REJECTED, or one of
        the order-manager conflict outcomes (IGNORED_*, DIRECTION_CONFLICT,
        POSITION_EXISTS_*) — so `/signals` can join and show it per signal."""
        order = LiveOrder(
            live_order_id  = live_order_id,
            signal_id      = candidate.signal_id,
            symbol         = candidate.symbol,
            side           = "BUY" if candidate.direction == "LONG" else "SELL",
            direction      = candidate.direction,
            entry          = candidate.entry,
            sl             = candidate.sl,
            tp             = (candidate.tp2 or candidate.tp1),
            notional       = str(self.effective_notional()),
            leverage       = 0,
            quantity       = "0",
            status         = status,
            entry_order_id = None,
            sl_order_id    = None,
            tp_order_id    = None,
            created_at     = now,
            updated_at     = now,
            reject_reason  = reason,
            strategy_id    = self._strategy_id,
        )
        try:
            self._repo.save(order)
            self._event(live_order_id, candidate.signal_id, status, {"reason": reason})
        except Exception:
            log.exception("[Live] failed to save %s order", status)

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

    # ── Orphan-order reconciliation sweep ───────────────────────────────────────

    def sweep_orphans(self) -> dict:
        """Detect Binance orders that exist on the exchange but aren't tracked
        by ANY live_orders row (across both V3 and V66 — deliberately
        unscoped by strategy_id here, since an orphan could have come from
        either strategy, a manual trade, or a previous bug).

        This is diagnostic-only: it never auto-cancels anything. Given this
        controls real money, a human must confirm before any order is
        touched — we just surface the finding so the operator can act via
        /orders, /livestatus, or manually on Binance/Telegram.
        """
        result = {"checked": 0, "orphans": []}
        try:
            open_orders = self._client.get_open_orders()
        except Exception:
            log.exception("[Live] sweep_orphans: get_open_orders failed")
            return result

        result["checked"] = len(open_orders)
        if not open_orders:
            return result

        try:
            # Unscoped: every non-terminal order across all strategies.
            tracked = self._repo.load_by_status("PENDING", "FILLED")
        except Exception:
            log.exception("[Live] sweep_orphans: failed to load tracked orders")
            return result

        known_order_ids: set[str] = set()
        for o in tracked:
            for oid in (o.entry_order_id, o.sl_order_id, o.tp_order_id):
                if oid:
                    known_order_ids.add(str(oid))

        orphans = [
            oo for oo in open_orders
            if str(oo.get("orderId", "")) not in known_order_ids
        ]
        result["orphans"] = orphans

        if orphans:
            log.warning("[Live] sweep_orphans found %d untracked order(s): %s",
                        len(orphans),
                        [(o.get("symbol"), o.get("orderId"), o.get("type")) for o in orphans])
            lines = [f"⚠️ 发现 {len(orphans)} 个未被系统追踪的挂单（可能是手动下单/历史遗留）："]
            for o in orphans[:10]:
                lines.append(
                    f"  {o.get('symbol')} orderId={o.get('orderId')} "
                    f"{o.get('type')} {o.get('side')} qty={o.get('origQty')}"
                )
            lines.append("该检查仅提示，不会自动撤单，请人工核实。")
            self._notify("\n".join(lines))

        return result

    def cleanup_dangling_algo_orders(self) -> dict:
        """Safety net: for orders already in a terminal DB status, re-check
        any leftover sl_order_id/tp_order_id and cancel them on Binance if
        still open. Covers the case where the original cancel-on-terminal
        call failed (network blip, transient API error) and left a
        conditional order stranded with no position behind it.

        Only touches orders whose status is already unambiguously terminal
        in our own DB — never guesses at live/ambiguous state.
        """
        result = {"checked": 0, "cleaned": 0}
        try:
            stale = self._repo.load_terminal_with_dangling_algo()
        except Exception:
            log.exception("[Live] cleanup_dangling_algo_orders: failed to load candidates")
            return result

        result["checked"] = len(stale)
        for order in stale:
            cleared_sl = cleared_tp = False
            if order.sl_order_id:
                cleared_sl = self._cancel_and_confirm(order.symbol, order.sl_order_id, "SL")
            if order.tp_order_id:
                cleared_tp = self._cancel_and_confirm(order.symbol, order.tp_order_id, "TP")
            if cleared_sl or cleared_tp:
                try:
                    self._repo.clear_algo_ids(order.live_order_id, cleared_sl, cleared_tp)
                    result["cleaned"] += 1
                    log.info("[Live] cleanup_dangling_algo_orders: cleared stray leg(s) for %s (%s)",
                              order.live_order_id, order.symbol)
                except Exception:
                    log.exception("[Live] cleanup_dangling_algo_orders: failed to clear ids for %s",
                                  order.live_order_id)
        return result

    def _cancel_and_confirm(self, symbol: str, algo_id: str, label: str) -> bool:
        """Try to cancel a dangling algo order. Returns True if it's now
        confirmed gone (canceled, or already gone/unknown), False if the
        cancel attempt itself failed for an unexpected reason (leave it for
        the next sweep pass rather than silently dropping tracking of it)."""
        try:
            self._client.cancel_algo_order(symbol, algo_id)
            return True
        except BinanceFuturesError as exc:
            if exc.code in (-2011, -2013) or "unknown" in exc.msg.lower():
                return True
            log.warning("[Live] cleanup: cancel %s algo order %s for %s failed: %s",
                        label, algo_id, symbol, exc)
            return False
        except Exception:
            log.exception("[Live] cleanup: unexpected error canceling %s algo order %s for %s",
                           label, algo_id, symbol)
            return False

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
