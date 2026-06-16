from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP

from binance_ai_trader.ai_macro.models import AIMacroScore
from binance_ai_trader.domain.models import Kline, Ticker24h

MIN_SCORE = 80
MAX_STOP_PCT = Decimal("8")


def score_candidate(
    symbol: str,
    direction: str,
    ticker: Ticker24h,
    klines_15m: tuple[Kline, ...],
    now: datetime | None = None,
) -> AIMacroScore:
    """Score a single candidate. Returns direction=PASS when not viable."""
    if len(klines_15m) < 21:
        return _pass(symbol, reason="insufficient_klines")

    closes = tuple(k.close for k in klines_15m)
    ema20 = _ema(closes, 20)

    if len(klines_15m) < 15:
        return _pass(symbol, reason="insufficient_atr_data")
    atr14 = _atr(klines_15m, 14)
    if atr14 <= 0:
        return _pass(symbol, reason="zero_atr")

    recent = klines_15m[-21:-1]
    swing_high = max(k.high for k in recent)
    swing_low = min(k.low for k in recent)
    current = klines_15m[-1].close

    buffer = atr14 * Decimal("0.25")
    if direction == "LONG":
        entry = _price(min(ema20, current - buffer))
        stop = _price(min(swing_low, entry - atr14))
        if entry <= 0 or stop <= 0 or stop >= entry:
            return _pass(symbol, reason="invalid_long_levels")
        risk = entry - stop
        tp1 = _price(entry + risk)
        tp2 = _price(entry + risk * Decimal("2"))
    else:
        entry = _price(max(ema20, current + buffer))
        stop = _price(max(swing_high, entry + atr14))
        if entry <= 0 or stop <= entry:
            return _pass(symbol, reason="invalid_short_levels")
        risk = stop - entry
        tp1 = _price(entry - risk)
        tp2 = _price(entry - risk * Decimal("2"))

    if risk <= 0:
        return _pass(symbol, reason="zero_risk")

    stop_pct = risk / entry * Decimal("100")
    if stop_pct > MAX_STOP_PCT:
        return AIMacroScore(
            symbol=symbol, direction="PASS", score=0,
            trend_score=0, momentum_score=0, volume_score=0,
            structure_score=0, risk_score=0,
            reason=f"stop_too_wide:{float(stop_pct):.1f}%>8%",
            entry=entry, stop_loss=stop, tp1=tp1, tp2=tp2,
        )

    rr = Decimal("2")
    trend_score = _score_trend(direction, current, ema20)
    momentum_score = _score_momentum(abs(ticker.price_change_percent))
    volume_score = _score_volume(ticker.quote_volume)
    structure_score = _score_structure(rr)
    risk_score = _score_risk(stop_pct)
    total = trend_score + momentum_score + volume_score + structure_score + risk_score

    reason = (
        f"score={total}: trend={trend_score} momentum={momentum_score} "
        f"volume={volume_score} structure={structure_score} risk={risk_score}; "
        f"stop_pct={float(stop_pct):.1f}%"
    )

    if total < MIN_SCORE:
        return AIMacroScore(
            symbol=symbol, direction="PASS", score=total,
            trend_score=trend_score, momentum_score=momentum_score,
            volume_score=volume_score, structure_score=structure_score,
            risk_score=risk_score, reason=reason,
            entry=entry, stop_loss=stop, tp1=tp1, tp2=tp2,
        )

    return AIMacroScore(
        symbol=symbol, direction=direction, score=total,
        trend_score=trend_score, momentum_score=momentum_score,
        volume_score=volume_score, structure_score=structure_score,
        risk_score=risk_score, reason=reason,
        entry=entry, stop_loss=stop, tp1=tp1, tp2=tp2,
    )


def _pass(symbol: str, reason: str) -> AIMacroScore:
    return AIMacroScore(
        symbol=symbol, direction="PASS", score=0,
        trend_score=0, momentum_score=0, volume_score=0,
        structure_score=0, risk_score=0, reason=reason,
        entry=None, stop_loss=None, tp1=None, tp2=None,
    )


def _score_trend(direction: str, current: Decimal, ema20: Decimal) -> int:
    if ema20 <= 0:
        return 0
    diff_pct = (current - ema20) / ema20 * Decimal("100")
    if direction == "LONG":
        if diff_pct >= 0:
            return 20
        if diff_pct >= -2:
            return 15
        if diff_pct >= -5:
            return 10
        return 5
    else:
        if diff_pct <= 0:
            return 20
        if diff_pct <= 2:
            return 15
        if diff_pct <= 5:
            return 10
        return 5


def _score_momentum(abs_change: Decimal) -> int:
    if abs_change >= 30:
        return 20
    if abs_change >= 20:
        return 17
    if abs_change >= 15:
        return 14
    if abs_change >= 10:
        return 10
    if abs_change >= 5:
        return 6
    return 3


def _score_volume(quote_volume: Decimal) -> int:
    if quote_volume >= 100_000_000:
        return 20
    if quote_volume >= 50_000_000:
        return 18
    if quote_volume >= 20_000_000:
        return 15
    if quote_volume >= 10_000_000:
        return 12
    if quote_volume >= 5_000_000:
        return 8
    return 4


def _score_structure(rr: Decimal) -> int:
    if rr >= 3:
        return 20
    if rr >= Decimal("2.5"):
        return 17
    if rr >= 2:
        return 14
    if rr >= Decimal("1.5"):
        return 10
    if rr >= 1:
        return 5
    return 0


def _score_risk(stop_pct: Decimal) -> int:
    if stop_pct <= 2:
        return 20
    if stop_pct <= 3:
        return 17
    if stop_pct <= 5:
        return 14
    if stop_pct <= 7:
        return 10
    if stop_pct <= 8:
        return 5
    return 0


def _ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    if len(values) < period:
        raise ValueError("not enough values for EMA")
    multiplier = Decimal("2") / Decimal(period + 1)
    value = sum(values[:period], Decimal("0")) / Decimal(period)
    for v in values[period:]:
        value = (v - value) * multiplier + value
    return value


def _atr(klines: tuple[Kline, ...], period: int) -> Decimal:
    if len(klines) < period + 1:
        raise ValueError("not enough klines for ATR")
    ranges = []
    for prev, cur in zip(klines[-period - 1:-1], klines[-period:]):
        ranges.append(max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        ))
    return sum(ranges, Decimal("0")) / Decimal(period)


def _price(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP).normalize()
