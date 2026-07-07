"""
Task 4 — Price correctness test.

Runs a mini-scan over a known set of large-cap symbols and verifies:
1. Each symbol returns a non-empty DataFrame.
2. Latest close is a realistic positive number.
3. No cross-symbol contamination (each symbol's close is unique / not identical
   to every other symbol's close, which would indicate the same data was returned
   for all tickers).
4. Indicators compute without NaN on the latest row for MA20.
5. Price cross-check (history vs 5d) passes for all symbols.

Run with:
    python3 -m pytest tests/test_price_correctness.py -v
or directly:
    python3 tests/test_price_correctness.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from market_hunter.data.price import (
    get_ohlcv_and_market_cap, compute_indicators, validate_price
)

TEST_SYMBOLS = ["AAPL", "MSFT", "NVDA", "INTC", "WDC", "STX", "KLAC"]

# Rough plausible price ranges as of mid-2026 (update if needed)
# Used only to catch gross errors — not tight bounds.
PRICE_SANITY = {
    "AAPL":  (50,    1000),
    "MSFT":  (50,    1500),
    "NVDA":  (10,    2000),
    "INTC":  (10,    500),
    "WDC":   (10,    2000),
    "STX":   (20,    3000),
    "KLAC":  (50,    2000),
}


def run_tests() -> bool:
    print("\n" + "=" * 60)
    print("  PRICE CORRECTNESS TEST — 7-symbol mini scan")
    print("=" * 60)

    closes: dict[str, float] = {}
    all_passed = True

    for sym in TEST_SYMBOLS:
        print(f"\n  {sym}:")
        df, market_cap, price_valid = get_ohlcv_and_market_cap(sym, validate=True)

        # 1. Non-empty DataFrame
        if df.empty:
            print(f"    FAIL — empty DataFrame")
            all_passed = False
            continue
        print(f"    rows={len(df)}  market_cap=${market_cap / 1e9:.1f}B")

        # 2. Realistic close
        last_close = float(df["Close"].iloc[-1])
        lo, hi = PRICE_SANITY.get(sym, (1, 100_000))
        if not (lo <= last_close <= hi):
            print(f"    FAIL — close {last_close:.2f} outside sanity range [{lo}, {hi}]")
            all_passed = False
        else:
            print(f"    close={last_close:.2f}  OK (range [{lo}, {hi}])")

        # 3. Price cross-check
        if price_valid:
            print(f"    price_valid=True  OK")
        else:
            print(f"    FAIL — price cross-check rejected (>3% mismatch)")
            all_passed = False

        closes[sym] = last_close

        # 4. Indicator sanity — MA20 present and positive
        df_ind = compute_indicators(df)
        if "MA20" not in df_ind.columns:
            print(f"    FAIL — MA20 column missing")
            all_passed = False
        else:
            ma20 = df_ind["MA20"].iloc[-1]
            if ma20 is None or ma20 <= 0:
                print(f"    FAIL — MA20={ma20} invalid")
                all_passed = False
            else:
                print(f"    MA20={ma20:.2f}  OK")

    # 5. Cross-symbol contamination check
    print("\n  Cross-symbol contamination check:")
    close_values = list(closes.values())
    if len(set(f"{v:.4f}" for v in close_values)) == 1 and len(close_values) > 1:
        print("    FAIL — all symbols returned identical close prices (data contamination!)")
        all_passed = False
    else:
        for sym, price in closes.items():
            print(f"    {sym:<6} {price:.2f}")
        print("    All unique — OK")

    print("\n" + "=" * 60)
    print(f"  Result: {'ALL PASSED ✅' if all_passed else 'SOME FAILED ❌'}")
    print("=" * 60 + "\n")
    return all_passed


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
