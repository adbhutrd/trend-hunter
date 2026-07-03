"""Shared pytest fixtures.

* One temp DuckDB per test (`tmp_path` fixture)
* Reset Settings cache between tests (env variables stick around)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings


@pytest.fixture()
def storage(tmp_path: Path) -> DuckDBStorage:
    """A fresh DuckDBStorage pointed at a temp DB; auto-closed on teardown."""
    db = tmp_path / "trends.duckdb"
    # settings.db_path is loaded once and cached; for tests we instantiate
    # DuckDBStorage directly so the env-var global doesn't interfere.
    s = DuckDBStorage(db, read_only=False)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Settings is lru_cache'd — clear between tests so env mutation works."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
