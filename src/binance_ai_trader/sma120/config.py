"""SMA120 V1.9-D — strategy constants.

Parameters frozen. Do NOT modify SL_DISTANCE, TP_DISTANCE, ATR_MIN, ATR_MAX,
MAX_EXTENSION_ATR, or MAX_DAILY_TRADES without a full strategy re-evaluation.
"""
from decimal import Decimal

STRATEGY_ID = "xau_sma120"
SYMBOL      = "XAUUSDT"

SL_DISTANCE      = Decimal("8")     # fixed SL in price points — NEVER CHANGE
TP_DISTANCE      = Decimal("16")    # fixed TP in price points — NEVER CHANGE (1:2 RR)
ATR_MIN          = Decimal("4.00")  # ATR filter lower bound   — NEVER CHANGE
ATR_MAX          = Decimal("6.67")  # ATR filter upper bound   — NEVER CHANGE
MAX_EXTENSION_ATR = 3               # max extension from SMA120 in ATR multiples
MAX_DAILY_TRADES = 3                # max new paper orders per UTC calendar day

ATR_PERIOD  = 14
EMA_FAST    = 20
EMA_SLOW    = 60
SMA_PERIOD  = 120

M5_INTERVAL  = "5m"
M15_INTERVAL = "15m"
H1_INTERVAL  = "1h"

M5_LIMIT  = 165   # 120 (SMA) + 14 (ATR warmup) + buffer
M15_LIMIT = 80    # 60 (EMA60) + buffer
H1_LIMIT  = 80    # 60 (EMA60) + buffer

HOLD_HOURS     = 24
SIGNAL_PREFIX  = "SMA"
