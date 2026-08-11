"""K1_SHADOW_V2 and K2_SHADOW_V2 — parallel paper-only shadow strategies.

Inherits ALL conditions from production K1/K2.
Adds a small set of extra confirmation gates.
Used for controlled comparison only — NEVER touches live orders.

K1_SHADOW_V2 extra conditions (applied AFTER K1 passes):
  1. signal_candle.close > signal_candle.open          → CANDLE_NOT_BULLISH
  2. signal_candle.close > EMA20_15m                   → NOT_ABOVE_EMA20
  3. signal_candle.close > previous_15m.high           → NOT_BREAK_PREVIOUS_HIGH

K2_SHADOW_V2 extra condition (applied AFTER K2 passes):
  1. range_position_30d >= 0.35                        → RANGE_POS_TOO_LOW
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from binance_ai_trader.classic.indicators import ema_from_klines
from binance_ai_trader.domain.models import Kline

# ── Strategy IDs & names ──────────────────────────────────────────────────────
K1_SHADOW_V2_ID   = "classic_k1_shadow_v2"
K2_SHADOW_V2_ID   = "classic_k2_shadow_v2"
K1_SHADOW_V2_NAME = "K1 Shadow V2 底部启动确认"
K2_SHADOW_V2_NAME = "K2 Shadow V2 主升回踩确认"

_SOURCE_STRATEGY = {
    K1_SHADOW_V2_ID: "classic_k1",
    K2_SHADOW_V2_ID: "classic_k2",
}


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ShadowResult:
    shadow_strategy:           str
    source_strategy:           str
    symbol:                    str
    direction:                 str
    decision:                  str          # "PASS" or "REJECT"
    reject_reason:             str          # comma-joined reasons, "" if PASS

    # K1 extras (None for K2)
    signal_candle_open:        float | None = None
    signal_candle_close:       float | None = None
    signal_candle_change_pct:  float | None = None
    signal_candle_above_ema20: bool  | None = None
    break_previous_high:       bool  | None = None
    vol_ratio_15m:             float | None = None

    # K2 extras (also populated for K1 for completeness)
    range_position_30d:        float | None = None


# ── K1 Shadow V2 ─────────────────────────────────────────────────────────────

def evaluate_k1_shadow_v2(
    klines_15m: tuple[Kline, ...],
    vol_ratio_15m: float,
    range_pos_30d: float,
    symbol: str,
) -> ShadowResult:
    """Evaluate K1_SHADOW_V2 extra conditions on the last closed 15m candle.

    Must be called ONLY when production K1 has already passed all its checks.
    Uses the same klines slice the scanner already computed (open bar stripped).
    """
    if len(klines_15m) < 2:
        return ShadowResult(
            shadow_strategy=K1_SHADOW_V2_ID,
            source_strategy="classic_k1",
            symbol=symbol,
            direction="LONG",
            decision="REJECT",
            reject_reason="NOT_ENOUGH_KLINES",
            vol_ratio_15m=vol_ratio_15m,
            range_position_30d=range_pos_30d,
        )

    cur_k  = klines_15m[-1]   # last fully-closed 15m bar (signal candle)
    prev_k = klines_15m[-2]   # second-to-last closed bar
    ema20  = ema_from_klines(klines_15m, 20)

    rejs: list[str] = []

    # 1. Signal candle must be bullish
    candle_bullish = cur_k.close > cur_k.open
    if not candle_bullish:
        rejs.append("CANDLE_NOT_BULLISH")

    # 2. Close must be above EMA20
    #    (K1 already requires this; tracked separately for completeness)
    above_ema20 = cur_k.close >= ema20
    if not above_ema20:
        rejs.append("NOT_ABOVE_EMA20")

    # 3. Close must break above previous bar's high
    breaks_prev_high = cur_k.close > prev_k.high
    if not breaks_prev_high:
        rejs.append("NOT_BREAK_PREVIOUS_HIGH")

    open_f  = float(cur_k.open)
    close_f = float(cur_k.close)
    chg_pct = (close_f - open_f) / open_f * 100 if open_f != 0 else 0.0

    return ShadowResult(
        shadow_strategy=K1_SHADOW_V2_ID,
        source_strategy="classic_k1",
        symbol=symbol,
        direction="LONG",
        decision="PASS" if not rejs else "REJECT",
        reject_reason=",".join(rejs),
        signal_candle_open=open_f,
        signal_candle_close=close_f,
        signal_candle_change_pct=chg_pct,
        signal_candle_above_ema20=above_ema20,
        break_previous_high=breaks_prev_high,
        vol_ratio_15m=vol_ratio_15m,
        range_position_30d=range_pos_30d,
    )


# ── K2 Shadow V2 ─────────────────────────────────────────────────────────────

def evaluate_k2_shadow_v2(
    range_pos_30d: float,
    symbol: str,
) -> ShadowResult:
    """Evaluate K2_SHADOW_V2 extra condition.

    Must be called ONLY when production K2 has already passed all its checks.
    """
    rejs: list[str] = []

    if range_pos_30d < 0.35:
        rejs.append("RANGE_POS_TOO_LOW")

    return ShadowResult(
        shadow_strategy=K2_SHADOW_V2_ID,
        source_strategy="classic_k2",
        symbol=symbol,
        direction="LONG",
        decision="PASS" if not rejs else "REJECT",
        reject_reason=",".join(rejs),
        range_position_30d=range_pos_30d,
    )
