"""Wave strategy 共享工具函数。"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient, Kline

# ── 稳定币 / 杠杆代币关键词 ───────────────────────────────────────────────
_STABLE   = {"USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDD", "EURC", "AEUR"}
_LEVERAGE = {"UP", "DOWN", "BULL", "BEAR"}


# ── EMA ───────────────────────────────────────────────────────────────────

def ema(values: Sequence[Decimal], period: int) -> Decimal:
    k = Decimal(2) / Decimal(period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


# ── 量比 (最后一根 vs 前 period 根平均) ──────────────────────────────────

def vol_ratio(klines: Sequence[Kline], period: int = 20) -> Decimal:
    """最后一根 K 线成交量 / 前 period 根平均成交量。不含最后一根计算均量。"""
    if len(klines) < period + 1:
        return Decimal("0")
    ref = klines[-period - 1: -1]
    avg = sum(k.volume for k in ref) / Decimal(period)
    if avg == 0:
        return Decimal("0")
    return klines[-1].volume / avg


def vol_ma(klines: Sequence[Kline], period: int = 20) -> Decimal:
    """前 period 根 K 线的平均成交量（不含最后一根）。"""
    if len(klines) < period + 1:
        return Decimal("0")
    ref = klines[-period - 1: -1]
    return sum(k.volume for k in ref) / Decimal(period)


# ── 平台高点 / 低点（最后一根 K 线之前的 lookback 根）─────────────────────

def platform(klines: Sequence[Kline], lookback: int = 20) -> tuple[Decimal, Decimal]:
    """返回 (platform_high, platform_low)，基于最后一根之前的 lookback 根 K 线。"""
    ref = klines[-lookback - 1: -1]
    if not ref:
        return Decimal("0"), Decimal("0")
    return max(k.high for k in ref), min(k.low for k in ref)


# ── Swing High / Low ──────────────────────────────────────────────────────

def swing_low(klines: Sequence[Kline], lookback: int = 10) -> Decimal:
    return min(k.low for k in klines[-lookback:])


def swing_high(klines: Sequence[Kline], lookback: int = 10) -> Decimal:
    return max(k.high for k in klines[-lookback:])


# ── Top-N 币池 ────────────────────────────────────────────────────────────

def top_n_usdt_symbols(client: BinancePublicClient, top_n: int = 100) -> list[str]:
    """返回 rolling 24H 成交额前 top_n 的 USDT 永续合约 symbol 列表。
    过滤稳定币、杠杆代币。出错时返回空列表（调用方跳过本轮扫描）。
    """
    tickers = client.tickers_24h()
    keep: list[tuple[str, Decimal]] = []
    for t in tickers:
        sym  = t.symbol
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if any(kw in base for kw in _STABLE):
            continue
        if any(base.endswith(kw) or base.startswith(kw) for kw in _LEVERAGE):
            continue
        keep.append((sym, t.quote_volume))
    keep.sort(key=lambda x: x[1], reverse=True)
    return [sym for sym, _ in keep[:top_n]]


# ── 止损合规检查 ──────────────────────────────────────────────────────────

def clamp_stop(entry: Decimal, raw_stop: Decimal, direction: str) -> Decimal | None:
    """
    确保止损在 entry 的 2%-5% 范围内。
    超出 5% → 返回 None（信号废弃）。
    小于 2% → 强制扩展到 2%。
    """
    if direction == "LONG":
        pct = (entry - raw_stop) / entry
        if pct > Decimal("0.05"):
            return None   # 超出5%，废弃
        if pct < Decimal("0.02"):
            return entry * Decimal("0.98")   # 扩展到2%
        return raw_stop
    else:  # SHORT
        pct = (raw_stop - entry) / entry
        if pct > Decimal("0.05"):
            return None
        if pct < Decimal("0.02"):
            return entry * Decimal("1.02")
        return raw_stop
