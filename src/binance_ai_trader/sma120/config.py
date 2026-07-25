"""SMA120 V1.9-D — strategy constants.

Parameters frozen. Do NOT modify SL_DISTANCE, TP_DISTANCE, ATR_MIN, ATR_MAX,
MAX_EXTENSION_ATR, or MAX_DAILY_TRADES without a full strategy re-evaluation.
"""
from decimal import Decimal

STRATEGY_ID = "xau_sma120"
SYMBOL      = "XAUUSDT"

SL_DISTANCE      = Decimal("8")     # fixed SL in price points (~0.2% of $4000 gold)
TP_DISTANCE      = Decimal("16")    # fixed TP in price points — 1:2 RR
# ATR gate calibrated on XAUUSDT M5 at ~$4000 gold (Jul 2026):
# observed ATR distribution: p25=0.24  p50=0.33  p75=0.39  p90=0.52
# Lower bound filters dead/gap candles; upper bound filters extreme spikes
ATR_MIN          = Decimal("0.10")  # ATR filter lower bound
ATR_MAX          = Decimal("2.00")  # ATR filter upper bound
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
