"""Binance AI Trader V3 — unified pipeline core.

V3 goals:
  - All strategies output Candidates only.
  - Shared Risk Engine, Dedup Engine, Push Queue, Settlement, Performance.
  - Every candidate has a Global Signal ID (e.g. HOT-20260704-000001).
  - No symbol+entry JOINs — everything linked via signal_id.
"""
