import os

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MIN_MARKET_CAP = 10_000_000_000
MIN_AVG_DOLLAR_VOLUME = 50_000_000

LOOKBACK_DAYS = 300
MA60_PULLBACK_WINDOW = 15
MA60_TOLERANCE = 0.03
STRONG_TREND_52W_DISTANCE = 0.15
STRONG_TREND_PULLBACK_TOLERANCE = 0.05
NEW_HIGH_VOLUME_MULTIPLIER = 1.5

SCORE_WEIGHTS = {
    "trend_score": 30,
    "relative_strength_score": 25,
    "volume_score": 20,
    "pullback_risk_score": 15,
    "sector_score": 10,
}

SCHEDULER_HOUR_MY = 6
SCHEDULER_MINUTE_MY = 30
SCHEDULER_TZ = "Asia/Kuala_Lumpur"

DB_PATH = os.getenv("DB_PATH", "market_hunter.db")

# Cooldown periods per strategy (calendar days).
# During cooldown: signal is saved to DB but NOT sent to Telegram again.
COOLDOWN_DAYS: dict[str, int] = {
    "ma60_reclaim": 15,
    "strong_trend": 10,
    "new_high":     20,
}
