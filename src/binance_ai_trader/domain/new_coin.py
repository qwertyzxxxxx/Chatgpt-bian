"""New-coin detection utilities.

A symbol is classified as a "new coin" when it lacks sufficient historical
4h data for the SpaceEngine (which requires 720 bars ≈ 120 days).

Usage
-----
    info = classify_new_coin("NEWUSDT", repository)
    if info.is_new_coin:
        log.info("skipped_new_coin_insufficient_history: %s", info.report())
"""
from __future__ import annotations

import time
from dataclasses import dataclass

REQUIRED_4H_BARS = 720
REQUIRED_1D_BARS = 120


@dataclass(frozen=True, slots=True)
class NewCoinInfo:
    symbol: str
    history_4h_bars: int
    history_1d_bars: int
    is_new_coin: bool
    listing_age_days: int | None = None

    def skip_reason(self) -> str:
        return (
            f"skipped_new_coin_insufficient_history "
            f"(history_4h_bars={self.history_4h_bars}/{REQUIRED_4H_BARS})"
        )

    def report(self) -> str:
        age = f", listing_age_days={self.listing_age_days}" if self.listing_age_days else ""
        return (
            f"space_score=MISSING, reason=insufficient_4h_history, "
            f"history_4h_bars={self.history_4h_bars}/{REQUIRED_4H_BARS}{age}"
        )


def classify_new_coin(
    symbol: str,
    repository,
    now_ms: int | None = None,
) -> NewCoinInfo:
    """Classify whether *symbol* has enough history to run space-score strategies.

    Parameters
    ----------
    symbol:
        The trading symbol (e.g. "BTCUSDT").
    repository:
        A MarketDataRepository instance.  Must support ``count_klines`` and
        optionally ``load_earliest_kline_open_ms``.
    now_ms:
        Override for the current epoch millisecond (used in tests).
    """
    h4_count = repository.count_klines(symbol, "4h")
    d1_count = repository.count_klines(symbol, "1d")

    listing_age_days: int | None = None
    try:
        earliest_ms = repository.load_earliest_kline_open_ms(symbol, "4h")
        if earliest_ms is not None:
            now = now_ms if now_ms is not None else (time.time_ns() // 1_000_000)
            listing_age_days = int((now - earliest_ms) / 86_400_000)
    except Exception:
        pass

    is_new = h4_count < REQUIRED_4H_BARS or (
        listing_age_days is not None and listing_age_days < REQUIRED_1D_BARS
    )

    return NewCoinInfo(
        symbol=symbol,
        history_4h_bars=h4_count,
        history_1d_bars=d1_count,
        is_new_coin=is_new,
        listing_age_days=listing_age_days,
    )
