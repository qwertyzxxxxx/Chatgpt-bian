from decimal import Decimal
import unittest

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.regime import MarketRegimeEngine, RegimeState


def regime_klines(
    symbol: str,
    direction: str,
    volatile: bool = False,
) -> dict[str, tuple[Kline, ...]]:
    return {
        interval: _series(symbol, interval, direction, volatile)
        for interval in ("15m", "1h", "4h")
    }


def _series(symbol: str, interval: str, direction: str, volatile: bool) -> tuple[Kline, ...]:
    values = []
    for index in range(60):
        if direction == "bull":
            close = Decimal("100") + Decimal(index) * Decimal("0.5")
        elif direction == "bear":
            close = Decimal("200") - Decimal(index) * Decimal("0.5")
        else:
            close = Decimal("100")
        spread = Decimal("10") if volatile else Decimal("0.5")
        values.append(
            Kline(
                symbol=symbol,
                interval=interval,
                open_time_ms=index * 60_000,
                close_time_ms=(index + 1) * 60_000 - 1,
                open=close,
                high=close + spread,
                low=close - spread,
                close=close,
                volume=Decimal("100"),
                quote_volume=Decimal("10000"),
                trade_count=100,
            )
        )
    return tuple(values)


class MarketRegimeEngineTest(unittest.TestCase):
    def test_classifies_bull_bear_and_range(self) -> None:
        engine = MarketRegimeEngine()
        self.assertEqual(RegimeState.BULL, engine.evaluate_asset("BTCUSDT", regime_klines("BTCUSDT", "bull")))
        self.assertEqual(RegimeState.BEAR, engine.evaluate_asset("BTCUSDT", regime_klines("BTCUSDT", "bear")))
        self.assertEqual(RegimeState.RANGE, engine.evaluate_asset("BTCUSDT", regime_klines("BTCUSDT", "range")))

    def test_high_atr_or_insufficient_data_is_observe(self) -> None:
        engine = MarketRegimeEngine()
        self.assertEqual(
            RegimeState.OBSERVE,
            engine.evaluate_asset("BTCUSDT", regime_klines("BTCUSDT", "bull", volatile=True)),
        )
        incomplete = regime_klines("BTCUSDT", "bull")
        incomplete["4h"] = incomplete["4h"][:50]
        self.assertEqual(RegimeState.OBSERVE, engine.evaluate_asset("BTCUSDT", incomplete))

    def test_short_term_conflict_is_observe(self) -> None:
        engine = MarketRegimeEngine()
        conflicted = regime_klines("BTCUSDT", "bull")
        conflicted["15m"] = regime_klines("BTCUSDT", "bear")["15m"]
        self.assertEqual(RegimeState.OBSERVE, engine.evaluate_asset("BTCUSDT", conflicted))

    def test_combines_btc_and_eth_deterministically(self) -> None:
        engine = MarketRegimeEngine()
        bull = engine.evaluate(
            regime_klines("BTCUSDT", "bull"),
            regime_klines("ETHUSDT", "bull"),
        )
        mixed = engine.evaluate(
            regime_klines("BTCUSDT", "bull"),
            regime_klines("ETHUSDT", "range"),
        )
        conflict = engine.evaluate(
            regime_klines("BTCUSDT", "bull"),
            regime_klines("ETHUSDT", "bear"),
        )
        self.assertEqual(("BULL", "BULL", "BULL"), (bull.btc_regime, bull.eth_regime, bull.combined_regime))
        self.assertEqual("RANGE", mixed.combined_regime)
        self.assertEqual("OBSERVE", conflict.combined_regime)


if __name__ == "__main__":
    unittest.main()
