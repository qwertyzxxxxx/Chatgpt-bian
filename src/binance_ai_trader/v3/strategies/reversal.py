"""hotlist_reversal — V-Reversal (山寨妖币反插针) strategy.

5-layer multi-timeframe extreme-reversal detector, paper-only:
  1D  ATR extremity   : today's range >= 1.5x ATR(14, 1D)
  4H  RSI extremity   : RSI(14) > 80 (short) or < 20 (long)
  1H  Bollinger(20,3) : high pierces upper band (short) / low pierces lower band (long)
  15m long wick       : wick ratio >= 60% of full candle range
  15m volume + OI     : volume >= 4x MA20(15m) AND OI 15m-drop >= 10%

Coin universe: exclude top-30 market-cap coins (MAIN_COIN_BLACKLIST) and
require 24h quote volume in [$30M, $200M].

Exits (encoded into CandidateInput.reason / consumed by the runner+settler):
  SL = pin extreme +/- 1.5x ATR(15m) buffer
  TP = 1:2.2 RR
  Breakeven-move at 0.7x SL distance once >=1 closed 15m candle OR >=5min held
  Forced close at 4h max hold -> TIMEOUT_FORCED
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.v3.candidates.repository import CandidateInput
from binance_ai_trader.v3.strategies import reversal_indicators as ind
from binance_ai_trader.v3.strategies.base import V3Strategy

log = logging.getLogger(__name__)

STRATEGY_ID = "hotlist_reversal"

# Top-30 market-cap coins — always traded by V3/V66, never by this strategy.
MAIN_COIN_BLACKLIST: frozenset[str] = frozenset({
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "TRX", "TON", "AVAX",
    "SHIB", "DOT", "LINK", "BCH", "NEAR", "LTC", "MATIC", "POL", "ICP", "UNI",
    "APT", "ETC", "XLM", "ATOM", "HBAR", "FIL", "CRO", "IMX", "SUI", "ARB",
    "OP", "VET", "MKR", "INJ", "RNDR", "TAO", "AAVE",
})

_MIN_QUOTE_VOLUME_24H = Decimal("30000000")   # $30M
_MAX_QUOTE_VOLUME_24H = Decimal("200000000")  # $200M

_ATR_1D_MULT      = Decimal("1.5")
_RSI_4H_SHORT     = Decimal("80")
_RSI_4H_LONG      = Decimal("20")
_BOLL_STD         = Decimal("3")
_WICK_MIN_RATIO   = Decimal("0.60")
_VOL_SPIKE_MULT   = Decimal("4.0")
_OI_DROP_MIN_PCT  = Decimal("10")
_ATR_SL_BUFFER    = Decimal("1.5")   # x ATR(15m) beyond the pin extreme
_RR               = Decimal("2.2")

MAX_HOLD_MINUTES         = 240   # 4h forced close -> TIMEOUT_FORCED
BREAKEVEN_TRIGGER_R      = Decimal("0.7")   # x SL distance to trigger breakeven move
BREAKEVEN_MIN_HOLD_MIN   = 5     # anti premature-breakeven: min minutes held
BREAKEVEN_MIN_CANDLES    = 1     # OR at least 1 closed 15m candle

# Ordered rejection-stage labels used for per-layer diagnostics (see
# generate_candidates' "rejected-by-stage" summary log line).
_REJECT_STAGES = (
    "1d_klines_insufficient",
    "1d_atr_unavailable",
    "1d_atr_not_extreme",
    "4h_klines_insufficient",
    "4h_rsi_unavailable",
    "4h_rsi_not_extreme",
    "1h_klines_insufficient",
    "1h_bollinger_unavailable",
    "1h_no_band_pierce",
    "15m_klines_insufficient",
    "15m_wick_too_small",
    "15m_volume_not_spiked",
    "oi_history_failed",
    "oi_history_insufficient",
    "oi_drop_insufficient",
    "15m_atr_unavailable",
    "risk_non_positive",
)


class HotlistStrategyReversal(V3Strategy):
    """V-Reversal: catches extreme wick-reversals on mid-cap altcoins."""

    def __init__(self, client: BinancePublicClient) -> None:
        self._client = client

    @property
    def strategy_id(self) -> str:
        return STRATEGY_ID

    def generate_candidates(self, now: datetime | None = None) -> list[CandidateInput]:
        generated_at = self._now(now)
        candidates: list[CandidateInput] = []
        stats: dict[str, int] = {k: 0 for k in _REJECT_STAGES}

        try:
            tickers = self._client.tickers_24h()
        except Exception as exc:
            log.warning("[REV] tickers_24h failed: %s", exc)
            return []

        symbols = self._select_universe(tickers)
        for symbol in symbols:
            try:
                cand = self._evaluate_symbol(symbol, generated_at, stats)
            except Exception as exc:
                log.warning("[REV] evaluate failed for %s: %s", symbol, exc)
                stats["exception"] = stats.get("exception", 0) + 1
                continue
            if cand is not None:
                candidates.append(cand)
                log.info("[REV] SIGNAL %s %s reason=%s", symbol, cand.direction, cand.reason)

        log.info(
            "[REV] %d candidates from %d symbols scanned | rejected-by-stage: %s",
            len(candidates), len(symbols),
            ", ".join(f"{k}={v}" for k, v in stats.items() if v),
        )
        return candidates

    def _select_universe(self, tickers) -> list[str]:
        out: list[str] = []
        for t in tickers:
            if not t.symbol.endswith("USDT"):
                continue
            base = t.symbol[:-4]
            if base in MAIN_COIN_BLACKLIST:
                continue
            if not (_MIN_QUOTE_VOLUME_24H <= t.quote_volume <= _MAX_QUOTE_VOLUME_24H):
                continue
            out.append(t.symbol)
        return out

    def _evaluate_symbol(
        self, symbol: str, now: datetime, stats: dict[str, int] | None = None
    ) -> CandidateInput | None:
        def _reject(stage: str) -> None:
            if stats is not None:
                stats[stage] = stats.get(stage, 0) + 1
            log.debug("[REV] %s rejected at stage=%s", symbol, stage)

        k_1d = self._client.klines(symbol, "1d", limit=20)
        if len(k_1d) < 15:
            _reject("1d_klines_insufficient")
            return None
        atr_1d = ind.atr(
            [k.high for k in k_1d], [k.low for k in k_1d], [k.close for k in k_1d], period=14
        )
        if atr_1d is None or atr_1d <= 0:
            _reject("1d_atr_unavailable")
            return None
        today_range = k_1d[-1].high - k_1d[-1].low
        if today_range < atr_1d * _ATR_1D_MULT:
            _reject("1d_atr_not_extreme")
            return None

        k_4h = self._client.klines(symbol, "4h", limit=20)
        if len(k_4h) < 15:
            _reject("4h_klines_insufficient")
            return None
        rsi_4h = ind.rsi([k.close for k in k_4h], period=14)
        if rsi_4h is None:
            _reject("4h_rsi_unavailable")
            return None

        direction: str | None = None
        if rsi_4h > _RSI_4H_SHORT:
            direction = "SHORT"
        elif rsi_4h < _RSI_4H_LONG:
            direction = "LONG"
        else:
            _reject("4h_rsi_not_extreme")
            return None

        k_1h = self._client.klines(symbol, "1h", limit=25)
        if len(k_1h) < 20:
            _reject("1h_klines_insufficient")
            return None
        boll = ind.bollinger([k.close for k in k_1h], period=20, num_std=_BOLL_STD)
        if boll is None:
            _reject("1h_bollinger_unavailable")
            return None
        _, upper, lower = boll
        if direction == "SHORT" and k_1h[-1].high <= upper:
            _reject("1h_no_band_pierce")
            return None
        if direction == "LONG" and k_1h[-1].low >= lower:
            _reject("1h_no_band_pierce")
            return None

        k_15m = self._client.klines(symbol, "15m", limit=25)
        if len(k_15m) < 21:
            _reject("15m_klines_insufficient")
            return None
        last = k_15m[-1]
        upper_wick, lower_wick = ind.wick_ratio(last.open, last.high, last.low, last.close)
        if direction == "SHORT" and upper_wick < _WICK_MIN_RATIO:
            _reject("15m_wick_too_small")
            return None
        if direction == "LONG" and lower_wick < _WICK_MIN_RATIO:
            _reject("15m_wick_too_small")
            return None

        vol_ma = ind.volume_ma([k.volume for k in k_15m], period=20)
        if vol_ma is None or vol_ma <= 0 or last.volume < vol_ma * _VOL_SPIKE_MULT:
            _reject("15m_volume_not_spiked")
            return None

        try:
            oi_hist = self._client.open_interest_history(symbol, limit=3)
        except Exception as exc:
            log.warning("[REV] open_interest_history failed for %s: %s", symbol, exc)
            _reject("oi_history_failed")
            return None
        if len(oi_hist) < 2:
            _reject("oi_history_insufficient")
            return None
        oi_points = [p[1] for p in oi_hist]
        drop = ind.oi_drop_pct(oi_points)
        if drop is None or drop > -_OI_DROP_MIN_PCT:
            _reject("oi_drop_insufficient")
            return None

        atr_15m = ind.atr(
            [k.high for k in k_15m], [k.low for k in k_15m], [k.close for k in k_15m], period=14
        )
        if atr_15m is None or atr_15m <= 0:
            _reject("15m_atr_unavailable")
            return None

        entry = last.close
        if direction == "SHORT":
            stop_loss = last.high + atr_15m * _ATR_SL_BUFFER
            risk = stop_loss - entry
            if risk <= 0:
                _reject("risk_non_positive")
                return None
            take_profit = entry - risk * _RR
        else:
            stop_loss = last.low - atr_15m * _ATR_SL_BUFFER
            risk = entry - stop_loss
            if risk <= 0:
                _reject("risk_non_positive")
                return None
            take_profit = entry + risk * _RR

        stop_pct = float(risk / entry * 100)
        reason = (
            f"1D_ATR_ext(range={today_range:.4g}>=1.5xATR={atr_1d:.4g}) "
            f"4H_RSI={rsi_4h:.1f} 1H_BB3σ_pierce 15m_wick={max(upper_wick, lower_wick):.2f} "
            f"vol={last.volume:.0f}(>={_VOL_SPIKE_MULT}xMA20={vol_ma:.0f}) OI_drop={drop:.1f}%"
        )

        return CandidateInput(
            strategy_id=STRATEGY_ID,
            symbol=symbol,
            direction=direction,
            entry=str(entry),
            sl=str(stop_loss),
            tp1=str(take_profit),
            tp2=str(take_profit),
            rr=str(_RR),
            confidence=float(_RR) / 5.0,
            stop_pct=round(stop_pct, 2),
            change_24h=None,
            quote_volume=None,
            volume_ratio=float(last.volume / vol_ma),
            atr=float(atr_15m),
            ema20=None,
            ema60=None,
            market_regime="reversal",
            reason=reason,
        )

    def features(self, inp: CandidateInput) -> dict:
        base = super().features(inp)
        base.update({
            "strategy_version": "hotlist_reversal_v1",
            "source": "v_reversal_5layer",
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "breakeven_trigger_r": str(BREAKEVEN_TRIGGER_R),
        })
        return base
