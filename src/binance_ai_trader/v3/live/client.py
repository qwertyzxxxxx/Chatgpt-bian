"""Binance USD-M Futures authenticated REST client.

Uses only stdlib (urllib + hmac + hashlib) — no external dependencies.
Supports: account info, order placement, cancellation, position queries.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import time
from decimal import Decimal, ROUND_DOWN
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

_BASE = "https://fapi.binance.com"


class BinanceFuturesError(RuntimeError):
    def __init__(self, code: int, msg: str) -> None:
        super().__init__(f"Binance error {code}: {msg}")
        self.code = code
        self.msg = msg


class BinanceFuturesClient:
    """Minimal Binance USD-M Futures REST client."""

    def __init__(self, api_key: str, api_secret: str, timeout: float = 10.0) -> None:
        self._key = api_key
        self._secret = api_secret
        self._timeout = timeout
        self._info_cache: dict[str, dict] = {}

    # ── Internals ─────────────────────────────────────────────────────────────

    def _ts(self) -> int:
        return int(time.time() * 1000)

    def _sign(self, params: dict) -> str:
        qs = urlencode(params)
        return _hmac.new(self._secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

    def _headers(self) -> dict:
        return {"X-MBX-APIKEY": self._key}

    def _parse(self, raw: bytes) -> object:
        data = json.loads(raw)
        if isinstance(data, dict) and "code" in data:
            try:
                code = int(data.get("code", 0))
            except (TypeError, ValueError):
                code = 0
            if code < 0:
                raise BinanceFuturesError(code, data.get("msg", "unknown"))
        return data

    def _get(self, path: str, params: dict | None = None, signed: bool = True) -> object:
        p = dict(params or {})
        if signed:
            p["timestamp"] = self._ts()
            p["signature"] = self._sign(p)
        url = f"{_BASE}{path}?{urlencode(p)}" if p else f"{_BASE}{path}"
        req = Request(url, headers=self._headers())
        try:
            with urlopen(req, timeout=self._timeout) as r:
                return self._parse(r.read())
        except HTTPError as exc:
            body = exc.read()
            try:
                data = json.loads(body)
                raise BinanceFuturesError(data.get("code", -1), data.get("msg", str(exc))) from exc
            except (ValueError, KeyError):
                raise BinanceFuturesError(-1, str(exc)) from exc

    def _post(self, path: str, params: dict) -> object:
        p = dict(params)
        p["timestamp"] = self._ts()
        p["signature"] = self._sign(p)
        body = urlencode(p).encode()
        req = Request(f"{_BASE}{path}", data=body, headers=self._headers(), method="POST")
        try:
            with urlopen(req, timeout=self._timeout) as r:
                return self._parse(r.read())
        except HTTPError as exc:
            raw = exc.read()
            try:
                data = json.loads(raw)
                raise BinanceFuturesError(data.get("code", -1), data.get("msg", str(exc))) from exc
            except (ValueError, KeyError):
                raise BinanceFuturesError(-1, str(exc)) from exc

    def _delete(self, path: str, params: dict) -> object:
        p = dict(params)
        p["timestamp"] = self._ts()
        p["signature"] = self._sign(p)
        url = f"{_BASE}{path}?{urlencode(p)}"
        req = Request(url, headers=self._headers(), method="DELETE")
        try:
            with urlopen(req, timeout=self._timeout) as r:
                return self._parse(r.read())
        except HTTPError as exc:
            raw = exc.read()
            try:
                data = json.loads(raw)
                raise BinanceFuturesError(data.get("code", -1), data.get("msg", str(exc))) from exc
            except (ValueError, KeyError):
                raise BinanceFuturesError(-1, str(exc)) from exc

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        return self._get("/fapi/v2/account")  # type: ignore[return-value]

    def get_balance(self) -> Decimal:
        """Return available USDT balance."""
        acc = self.get_account()
        for asset in acc.get("assets", []):
            if asset.get("asset") == "USDT":
                return Decimal(str(asset.get("availableBalance", "0")))
        return Decimal("0")

    def get_positions(self) -> list[dict]:
        """Return all positions with nonzero size."""
        data = self._get("/fapi/v2/positionRisk")
        return [p for p in data if Decimal(str(p.get("positionAmt", "0"))) != 0]  # type: ignore[union-attr]

    def get_position(self, symbol: str) -> dict | None:
        data = self._get("/fapi/v2/positionRisk", {"symbol": symbol})
        for p in data:  # type: ignore[union-attr]
            if Decimal(str(p.get("positionAmt", "0"))) != 0:
                return p
        return None

    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        p: dict = {}
        if symbol:
            p["symbol"] = symbol
        return self._get("/fapi/v1/openOrders", p)  # type: ignore[return-value]

    def get_order(self, symbol: str, order_id: str) -> dict:
        return self._get("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})  # type: ignore[return-value]

    def get_today_trades_summary(self) -> dict:
        """Return today's realized PnL (sum of income)."""
        ts_start = int(time.time() * 1000) - 86_400_000
        data = self._get("/fapi/v1/income", {
            "incomeType": "REALIZED_PNL",
            "startTime": ts_start,
            "limit": 1000,
        })
        total = sum(Decimal(str(x.get("income", "0"))) for x in data)  # type: ignore[union-attr]
        return {"realized_pnl": total}

    # ── Leverage ──────────────────────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        return self._post("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})  # type: ignore[return-value]

    def get_max_leverage(self, symbol: str) -> int:
        """Return max allowed leverage for a symbol (caps search at 20)."""
        try:
            brackets = self._get("/fapi/v1/leverageBracket", {"symbol": symbol})
            for item in brackets:  # type: ignore[union-attr]
                if item.get("symbol") == symbol:
                    # brackets list sorted by notional floor, first bracket has max leverage
                    bkts = item.get("brackets", [])
                    if bkts:
                        return int(bkts[0].get("initialLeverage", 10))
        except Exception:
            pass
        return 10

    # ── Symbol info ───────────────────────────────────────────────────────────

    def get_symbol_info(self, symbol: str) -> dict:
        if symbol in self._info_cache:
            return self._info_cache[symbol]
        info = self._get("/fapi/v1/exchangeInfo", signed=False)
        for s in info.get("symbols", []):  # type: ignore[union-attr]
            self._info_cache[s["symbol"]] = s
        return self._info_cache.get(symbol, {})

    def get_quantity_precision(self, symbol: str) -> int:
        info = self.get_symbol_info(symbol)
        return int(info.get("quantityPrecision", 3))

    def get_price_precision(self, symbol: str) -> int:
        info = self.get_symbol_info(symbol)
        return int(info.get("pricePrecision", 4))

    def get_tick_size(self, symbol: str) -> Decimal:
        """Return the PRICE_FILTER tickSize for a symbol (authoritative for rounding)."""
        info = self.get_symbol_info(symbol)
        for f in info.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                return Decimal(str(f["tickSize"]))
        prec = int(info.get("pricePrecision", 4))
        return Decimal(10) ** -prec

    def get_step_size(self, symbol: str) -> Decimal:
        """Return the LOT_SIZE stepSize for a symbol (authoritative for qty rounding)."""
        info = self.get_symbol_info(symbol)
        for f in info.get("filters", []):
            if f.get("filterType") == "LOT_SIZE":
                return Decimal(str(f["stepSize"]))
        prec = int(info.get("quantityPrecision", 3))
        return Decimal(10) ** -prec

    def get_ticker_price(self, symbol: str) -> Decimal:
        data = self._get("/fapi/v1/ticker/price", {"symbol": symbol}, signed=False)
        return Decimal(str(data.get("price", "0")))  # type: ignore[union-attr]

    def round_quantity(self, symbol: str, qty: Decimal) -> Decimal:
        step = self.get_step_size(symbol)
        return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        step = self.get_tick_size(symbol)
        return (price / step).to_integral_value(rounding=ROUND_DOWN) * step

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_limit(
        self, symbol: str, side: str, price: Decimal, quantity: Decimal,
        position_side: str = "BOTH",
    ) -> dict:
        return self._post("/fapi/v1/order", {  # type: ignore[return-value]
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "price": str(price),
            "quantity": str(quantity),
        })

    def place_entry_with_sltp(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        quantity: Decimal,
        tp_price: Decimal,
        sl_price: Decimal,
        position_side: str = "BOTH",
    ) -> dict:
        """Place entry LIMIT, then attach SL + TP as Algo (conditional) orders.

        Binance migrated STOP_MARKET/TAKE_PROFIT_MARKET off /fapi/v1/order and
        /fapi/v1/batchOrders to the dedicated Algo Order API on 2025-12-09
        (error -4120 "...Please use the Algo Order API endpoints instead").
        The algo endpoint has no batch variant, so SL/TP are placed as two
        separate calls right after the entry order (not truly atomic, but the
        window between them is milliseconds).

        Returns dict with entry_order_id, tp_order_id, sl_order_id.
        Raises BinanceFuturesError only if the ENTRY order itself fails.
        SL/TP partial failures are returned as empty strings (naked position fallback handles them).
        """
        close_side = "SELL" if side == "BUY" else "BUY"

        entry_order: dict = {
            "symbol":       symbol,
            "side":         side,
            "type":         "LIMIT",
            "price":        str(entry_price),
            "quantity":     str(quantity),
            "timeInForce":  "GTC",
        }
        if position_side != "BOTH":
            entry_order["positionSide"] = position_side
        else:
            entry_order["reduceOnly"] = "false"

        entry_resp = self._post("/fapi/v1/order", entry_order)  # type: ignore[assignment]
        entry_id = str(entry_resp.get("orderId", ""))  # type: ignore[union-attr]

        sl_id = tp_id = ""
        try:
            sl_resp = self.place_stop_market(symbol, close_side, sl_price, quantity, position_side=position_side)
            sl_id = str(sl_resp.get("algoId", sl_resp.get("orderId", "")))
        except BinanceFuturesError as exc:
            log.warning("[client] %s SL algo order failed: %s — sync fallback will retry", symbol, exc)
        try:
            tp_resp = self.place_take_profit_market(symbol, close_side, tp_price, quantity, position_side=position_side)
            tp_id = str(tp_resp.get("algoId", tp_resp.get("orderId", "")))
        except BinanceFuturesError as exc:
            log.warning("[client] %s TP algo order failed: %s — sync fallback will retry", symbol, exc)

        return {
            "entry_order_id": entry_id,
            "tp_order_id":    tp_id,
            "sl_order_id":    sl_id,
        }

    def _place_algo_conditional(
        self, symbol: str, side: str, order_type: str, stop_price: Decimal, quantity: Decimal,
        position_side: str = "BOTH",
    ) -> dict:
        """Place a STOP_MARKET / TAKE_PROFIT_MARKET conditional order via the
        Algo Order API (POST /fapi/v1/algoOrder, algoType=CONDITIONAL).

        Required since Binance's 2025-12-09 migration — the legacy
        /fapi/v1/order path now rejects these order types with -4120."""
        params: dict = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         order_type,
            "algoType":     "CONDITIONAL",
            "triggerPrice": str(stop_price),
            "quantity":     str(quantity),
            "workingType":  "MARK_PRICE",
        }
        if position_side == "BOTH":
            params["reduceOnly"] = "true"
        return self._post("/fapi/v1/algoOrder", params)  # type: ignore[return-value]

    def place_stop_market(
        self, symbol: str, side: str, stop_price: Decimal, quantity: Decimal,
        position_side: str = "BOTH",
    ) -> dict:
        """Place a STOP_MARKET stop-loss via the Algo Order API."""
        return self._place_algo_conditional(symbol, side, "STOP_MARKET", stop_price, quantity, position_side)

    def place_take_profit_market(
        self, symbol: str, side: str, stop_price: Decimal, quantity: Decimal,
        position_side: str = "BOTH",
    ) -> dict:
        """Place a TAKE_PROFIT_MARKET take-profit via the Algo Order API."""
        return self._place_algo_conditional(symbol, side, "TAKE_PROFIT_MARKET", stop_price, quantity, position_side)

    def get_algo_order(self, symbol: str, algo_id: str) -> dict:
        """Query a single algo (conditional SL/TP) order by algoId.

        Returns a dict with a normalized "status" key ("FILLED" once
        triggered/executed, else the raw algoStatus) so callers can treat it
        like the legacy get_order() response.
        """
        try:
            resp = self._get("/fapi/v1/algoOrder", {"symbol": symbol, "algoId": algo_id})
        except BinanceFuturesError:
            raise
        if isinstance(resp, dict):
            algo_status = resp.get("algoStatus", "")
            actual_order_id = resp.get("actualOrderId", "")
            resp["status"] = "FILLED" if (algo_status == "FILLED" or actual_order_id) else algo_status
        return resp  # type: ignore[return-value]

    def cancel_algo_order(self, symbol: str, algo_id: str) -> dict:
        return self._delete("/fapi/v1/algoOrder", {"symbol": symbol, "algoId": algo_id})  # type: ignore[return-value]

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self._delete("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})  # type: ignore[return-value]

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol})  # type: ignore[return-value]

    def close_position_market(
        self, symbol: str, side: str, quantity: Decimal, position_side: str = "BOTH"
    ) -> dict:
        """Market-close a position. side should be opposite of position side."""
        params: dict = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": str(quantity),
        }
        if position_side == "BOTH":
            params["reduceOnly"] = "true"
        return self._post("/fapi/v1/order", params)  # type: ignore[return-value]
