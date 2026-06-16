from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.ai_macro.models import MacroAnalysis
from binance_ai_trader.domain.models import Ticker24h

_RISK_OFF_THRESHOLD = Decimal("-5")
_BEAR_BTC = Decimal("-3")
_BEAR_ETH = Decimal("-2")
_BULL_BTC = Decimal("3")
_BULL_ETH = Decimal("2")
_BULL_STRONG_BTC = Decimal("5")


class MacroAnalyzer:
    """Classify market state from BTC/ETH 24h tickers. Research only."""

    def analyze(
        self,
        btc_ticker: Ticker24h,
        eth_ticker: Ticker24h,
        now: datetime | None = None,
    ) -> MacroAnalysis:
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        btc_chg = btc_ticker.price_change_percent
        eth_chg = eth_ticker.price_change_percent
        market_state, risk_grade, trade_bias = _classify(btc_chg, eth_chg)
        return MacroAnalysis(
            generated_at=generated_at.isoformat(timespec="seconds"),
            btc_change_pct=btc_chg,
            eth_change_pct=eth_chg,
            market_state=market_state,
            risk_grade=risk_grade,
            trade_bias=trade_bias,
        )


def _classify(btc_chg: Decimal, eth_chg: Decimal) -> tuple[str, str, str]:
    if btc_chg <= _RISK_OFF_THRESHOLD:
        return "RISK_OFF", "D", "NO_TRADE"
    if btc_chg <= _BEAR_BTC and eth_chg <= _BEAR_ETH:
        return "BEAR", "C", "SHORT_ONLY"
    if btc_chg >= _BULL_BTC and eth_chg >= _BULL_ETH:
        grade = "A" if btc_chg >= _BULL_STRONG_BTC else "B"
        return "BULL", grade, "LONG_ONLY"
    return "RANGE", "B", "BOTH"
