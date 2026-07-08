"""Tests for V3RuntimeSettingsRepository (live dedup/max-orders overrides)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from binance_ai_trader.v3.settings.repository import (
    DEFAULTS,
    V3_STRATEGY_ID,
    V66_STRATEGY_ID,
    V3RuntimeSettingsRepository,
)


class _FakeCursor:
    def __init__(self, store: dict):
        self._store = store
        self._fetch_result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT"):
            strategy_id = params[0]
            row = self._store.get(strategy_id)
            self._fetch_result = row
        elif sql_norm.startswith("INSERT"):
            strategy_id, dedup_hours, max_open_orders, updated_at, updated_by = params
            existing = self._store.get(strategy_id, (None, None, None, None))
            new_dedup = dedup_hours if dedup_hours is not None else existing[0]
            new_max = max_open_orders if max_open_orders is not None else existing[1]
            self._store[strategy_id] = (new_dedup, new_max, updated_at, updated_by)
        elif sql_norm.startswith("DELETE"):
            strategy_id = params[0]
            self._store.pop(strategy_id, None)

    def fetchone(self):
        return self._fetch_result


class _FakeConn:
    def __init__(self, store: dict):
        self._store = store

    def cursor(self):
        return _FakeCursor(self._store)

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def store():
    return {}


@pytest.fixture
def repo(store):
    with patch(
        "binance_ai_trader.v3.settings.repository.get_conn",
        side_effect=lambda: _FakeConn(store),
    ):
        yield V3RuntimeSettingsRepository()


def test_resolve_uses_defaults_when_no_override(repo):
    dedup_hours, max_open_orders = repo.resolve(V3_STRATEGY_ID)
    assert dedup_hours == DEFAULTS[V3_STRATEGY_ID]["dedup_hours"]
    assert max_open_orders == DEFAULTS[V3_STRATEGY_ID]["max_open_orders"]


def test_set_max_open_orders_overrides_resolve(repo):
    repo.set_max_open_orders(V3_STRATEGY_ID, 20, updated_by="123")
    dedup_hours, max_open_orders = repo.resolve(V3_STRATEGY_ID)
    assert max_open_orders == 20
    assert dedup_hours == DEFAULTS[V3_STRATEGY_ID]["dedup_hours"]


def test_set_dedup_hours_overrides_resolve(repo):
    repo.set_dedup_hours(V66_STRATEGY_ID, 12, updated_by="123")
    dedup_hours, max_open_orders = repo.resolve(V66_STRATEGY_ID)
    assert dedup_hours == 12
    assert max_open_orders == DEFAULTS[V66_STRATEGY_ID]["max_open_orders"]


def test_set_dedup_hours_rejects_invalid_value(repo):
    with pytest.raises(ValueError):
        repo.set_dedup_hours(V3_STRATEGY_ID, 7)


def test_set_max_open_orders_rejects_out_of_range(repo):
    with pytest.raises(ValueError):
        repo.set_max_open_orders(V3_STRATEGY_ID, 0)
    with pytest.raises(ValueError):
        repo.set_max_open_orders(V3_STRATEGY_ID, 999)


def test_reset_clears_overrides(repo):
    repo.set_max_open_orders(V3_STRATEGY_ID, 20)
    repo.set_dedup_hours(V3_STRATEGY_ID, 12)
    repo.reset(V3_STRATEGY_ID)
    dedup_hours, max_open_orders = repo.resolve(V3_STRATEGY_ID)
    assert dedup_hours == DEFAULTS[V3_STRATEGY_ID]["dedup_hours"]
    assert max_open_orders == DEFAULTS[V3_STRATEGY_ID]["max_open_orders"]


def test_strategies_are_independent(repo):
    repo.set_max_open_orders(V3_STRATEGY_ID, 20)
    _, v66_max = repo.resolve(V66_STRATEGY_ID)
    assert v66_max == DEFAULTS[V66_STRATEGY_ID]["max_open_orders"]
