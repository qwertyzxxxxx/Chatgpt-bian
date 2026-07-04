"""StorageRepository — unified facade for all V3 permanent storage.

Business modules call StorageRepository and never touch the underlying
database type directly.

  Cache (klines, universe) → SQLite via MarketDataRepository (unchanged)
  History (orders, signals) → PostgreSQL via this facade
"""
from __future__ import annotations

from binance_ai_trader.v3.candidates.repository import V3CandidateRepository
from binance_ai_trader.v3.feature_store.repository import V3FeatureStoreRepository
from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
from binance_ai_trader.v3.push_queue.repository import V3PushQueueRepository


class StorageRepository:
    """Single entry-point for all V3 permanent data.

    Usage::

        store = StorageRepository()
        store.candidates.save(inp, signal_id)
        store.orders.load_open()
        store.features.save(signal_id, strategy_id, features)
    """

    def __init__(self) -> None:
        self.candidates = V3CandidateRepository()
        self.push_queue = V3PushQueueRepository()
        self.orders     = V3PaperOrderRepository()
        self.features   = V3FeatureStoreRepository()
