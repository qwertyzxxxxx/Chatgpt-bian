"""Live Mirror CLI commands.

Usage:
    python -m binance_ai_trader live status
    python -m binance_ai_trader live orders
    python -m binance_ai_trader live positions
    python -m binance_ai_trader live cancel --symbol XXXUSDT
    python -m binance_ai_trader live cancel-all
    python -m binance_ai_trader live close --symbol XXXUSDT
    python -m binance_ai_trader live close-all
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="binance_ai_trader live")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status",      help="账户余额 + 持仓 + 挂单概览")
    sub.add_parser("orders",      help="当前挂单列表")
    sub.add_parser("positions",   help="当前持仓列表")
    sub.add_parser("cancel-all",  help="取消全部挂单")
    sub.add_parser("close-all",   help="市价平全部持仓")

    p_cancel = sub.add_parser("cancel", help="取消指定币种挂单")
    p_cancel.add_argument("--symbol", required=True)

    p_close = sub.add_parser("close", help="市价平指定币种持仓")
    p_close.add_argument("--symbol", required=True)

    args = parser.parse_args(argv if argv is not None else sys.argv[2:])

    client = _make_client()
    if client is None:
        return 1

    if args.cmd == "status":
        return _cmd_status(client)
    elif args.cmd == "orders":
        return _cmd_orders(client)
    elif args.cmd == "positions":
        return _cmd_positions(client)
    elif args.cmd == "cancel":
        return _cmd_cancel(client, args.symbol)
    elif args.cmd == "cancel-all":
        return _cmd_cancel_all(client)
    elif args.cmd == "close":
        return _cmd_close(client, args.symbol)
    elif args.cmd == "close-all":
        return _cmd_close_all(client)
    return 0


def _make_client():
    from binance_ai_trader.v3.live.client import BinanceFuturesClient
    key    = os.environ.get("BINANCE_API_KEY", "")
    secret = os.environ.get("BINANCE_API_SECRET", "")
    if not key or not secret:
        print("❌ BINANCE_API_KEY / BINANCE_API_SECRET 未设置", file=sys.stderr)
        return None
    return BinanceFuturesClient(key, secret)


def _cmd_status(client) -> int:
    from binance_ai_trader.v3.live.client import BinanceFuturesError
    try:
        acc      = client.get_account()
        orders   = client.get_open_orders()
        positions = client.get_positions()
        balance = avail = Decimal("0")
        unrealised = Decimal("0")
        for asset in acc.get("assets", []):
            if asset.get("asset") == "USDT":
                balance    = Decimal(str(asset.get("walletBalance", "0")))
                avail      = Decimal(str(asset.get("availableBalance", "0")))
                unrealised = Decimal(str(asset.get("unrealizedProfit", "0")))
                break
        sign = "+" if unrealised >= 0 else ""
        print(f"余额         {balance:.2f} USDT")
        print(f"可用余额     {avail:.2f} USDT")
        print(f"未实现PnL    {sign}{unrealised:.2f} USDT")
        print(f"当前挂单数   {len(orders)}")
        print(f"当前持仓数   {len(positions)}")
    except BinanceFuturesError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_orders(client) -> int:
    from binance_ai_trader.v3.live.client import BinanceFuturesError
    try:
        orders = client.get_open_orders()
        if not orders:
            print("当前无挂单")
            return 0
        for o in orders:
            print(
                f"{o.get('symbol'):<12} {o.get('side'):<5} {o.get('type'):<20}"
                f" qty={o.get('origQty')} price={o.get('price','—')}"
                f" stop={o.get('stopPrice','—')}  id={o.get('orderId')}"
            )
    except BinanceFuturesError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_positions(client) -> int:
    from binance_ai_trader.v3.live.client import BinanceFuturesError
    try:
        positions = client.get_positions()
        if not positions:
            print("当前无持仓")
            return 0
        for p in positions:
            amt  = Decimal(str(p.get("positionAmt", "0")))
            side = "LONG" if amt > 0 else "SHORT"
            upnl = Decimal(str(p.get("unRealizedProfit", "0")))
            sign = "+" if upnl >= 0 else ""
            print(
                f"{p.get('symbol'):<12} {side:<6} amt={abs(amt)}"
                f" entry={p.get('entryPrice','?')}  PnL={sign}{upnl:.2f}U"
            )
    except BinanceFuturesError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_cancel(client, symbol: str) -> int:
    from binance_ai_trader.v3.live.client import BinanceFuturesError
    try:
        orders = client.get_open_orders(symbol)
        if not orders:
            print(f"{symbol} 无挂单")
            return 0
        for o in orders:
            client.cancel_order(symbol, str(o.get("orderId")))
            print(f"已取消 {symbol} orderId={o.get('orderId')}")
    except BinanceFuturesError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_cancel_all(client) -> int:
    from binance_ai_trader.v3.live.client import BinanceFuturesError
    try:
        orders = client.get_open_orders()
        symbols = list({o.get("symbol") for o in orders})
        if not symbols:
            print("当前无挂单")
            return 0
        for sym in symbols:
            client.cancel_all_orders(sym)
            print(f"已取消 {sym} 全部挂单")
    except BinanceFuturesError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_close(client, symbol: str) -> int:
    from binance_ai_trader.v3.live.client import BinanceFuturesError
    try:
        pos = client.get_position(symbol)
        if not pos:
            print(f"{symbol} 无持仓")
            return 0
        amt  = Decimal(str(pos.get("positionAmt", "0")))
        close_side = "SELL" if amt > 0 else "BUY"
        qty = client.round_quantity(symbol, abs(amt))
        resp = client.close_position_market(symbol, close_side, qty)
        print(f"已市价平 {symbol} qty={qty} orderId={resp.get('orderId')}")
    except BinanceFuturesError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_close_all(client) -> int:
    from binance_ai_trader.v3.live.client import BinanceFuturesError
    try:
        positions = client.get_positions()
        if not positions:
            print("当前无持仓")
            return 0
        for p in positions:
            sym  = p.get("symbol")
            amt  = Decimal(str(p.get("positionAmt", "0")))
            close_side = "SELL" if amt > 0 else "BUY"
            qty = client.round_quantity(sym, abs(amt))
            resp = client.close_position_market(sym, close_side, qty)
            print(f"已市价平 {sym} qty={qty} orderId={resp.get('orderId')}")
    except BinanceFuturesError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    return 0
