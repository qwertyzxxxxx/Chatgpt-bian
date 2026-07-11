"""BTC 方向过滤工具 — 根据 BTC 1h EMA10/EMA20 判断市场方向。

规则:
  EMA10 > EMA20 by >0.3%  → BULL  → 仅允许多头信号
  EMA10 < EMA20 by >0.3%  → BEAR  → 仅允许空头信号
  |EMA10 - EMA20| ≤ 0.3%  → RANGING → 多空均允许

设计原则:
  - 阈值故意设窄 (0.3%)，保证大多数时间判为 RANGING，不过度压制信号
  - 任何异常（API 错误、数据不足）均 fallback → RANGING，安全失败
"""
from __future__ import annotations

import logging
from decimal import Decimal

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.v3.candidates.repository import CandidateInput

log = logging.getLogger(__name__)

_RANGING_THRESHOLD = Decimal("0.003")   # 0.3%


def _ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    k   = Decimal(2) / Decimal(period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def get_btc_regime(
    client: BinancePublicClient,
    threshold: Decimal = _RANGING_THRESHOLD,
) -> str:
    """返回 'BULL' / 'BEAR' / 'RANGING'。出错时安全回退到 RANGING。"""
    try:
        klines = client.klines("BTCUSDT", "1h", limit=30)
        if len(klines) < 20:
            log.warning("[BTC regime] 数据不足 (%d bars)，回退到 RANGING", len(klines))
            return "RANGING"
        closes = tuple(k.close for k in klines)
        ema10  = _ema(closes, 10)
        ema20  = _ema(closes, 20)
        diff   = (ema10 - ema20) / ema20
        if diff > threshold:
            regime = "BULL"
        elif diff < -threshold:
            regime = "BEAR"
        else:
            regime = "RANGING"
        log.debug(
            "[BTC regime] EMA10=%.2f EMA20=%.2f diff=%.4f%% → %s",
            float(ema10), float(ema20), float(diff * 100), regime,
        )
        return regime
    except Exception:
        log.exception("[BTC regime] 获取失败，回退到 RANGING")
        return "RANGING"


def apply_btc_regime_filter(
    candidates: list[CandidateInput],
    client: BinancePublicClient,
    strategy_tag: str = "",
) -> list[CandidateInput]:
    """根据 BTC 方向过滤 candidates。RANGING 时不过滤。"""
    if not candidates:
        return candidates

    regime = get_btc_regime(client)

    if regime == "RANGING":
        log.info("[%s] BTC 震荡 (RANGING)，多空信号均保留 (%d个)", strategy_tag, len(candidates))
        return candidates

    filtered = [c for c in candidates if c.direction == ("LONG" if regime == "BULL" else "SHORT")]
    removed  = len(candidates) - len(filtered)
    log.info(
        "[%s] BTC %s，过滤掉 %d 个逆向信号，保留 %d 个",
        strategy_tag, regime, removed, len(filtered),
    )
    return filtered
