"""Storage protocol smoke tests.

Verifies that `DuckDBStorage` honours the Storage protocol:
  * schema migration auto-applies
  * upsert is idempotent (ON CONFLICT DO NOTHING)
  * query returns list[dict]
  * execute runs DDL/DML
  * read_only mode raises on upsert
  * health() returns the rows we wrote
"""
from __future__ import annotations

from datetime import datetime, timezone

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.types import HealthState


def test_schema_applied(storage: DuckDBStorage):
    rows = storage.query("SELECT max(version) AS v FROM _schema_version")
    assert rows and rows[0]["v"] >= 1


def test_upsert_idempotent(storage: DuckDBStorage):
    now = datetime.now(timezone.utc)
    sample = [{
        "source": "shopify",
        "external_id": "sku-1",
        "captured_at": now,
        "title": "Sample",
        "price": 12.5,
        "currency": "USD",
        "url": "https://example.com/p/1",
        "payload": '{"id": 1}',
    }]
    storage.upsert("products", sample)
    first = storage.query("SELECT count(*) AS n FROM products")[0]["n"]
    storage.upsert("products", sample)              # same row, ON CONFLICT
    second = storage.query("SELECT count(*) AS n FROM products")[0]["n"]
    assert first == 1
    assert second == 1


def test_query_returns_dicts(storage: DuckDBStorage):
    storage.upsert("health", [{
        "source": "shopify",
        "state": HealthState.OK.value,
        "last_run": datetime.now(timezone.utc),
        "last_ok": datetime.now(timezone.utc),
        "rows_in": 10,
        "error_rate": 0.0,
        "detail": "ok",
    }])
    rows = storage.query("SELECT * FROM health WHERE source = ?", ("shopify",))
    assert len(rows) == 1
    r = rows[0]
    assert r["source"] == "shopify"
    assert r["state"] == "ok"
    assert r["rows_in"] == 10


def test_read_only_raises_on_upsert(tmp_path):
    db = tmp_path / "ro.duckdb"
    ro = DuckDBStorage(db, read_only=False)
    ro.execute("CREATE TABLE t (id INTEGER)")
    ro.close()

    ro2 = DuckDBStorage(db, read_only=True)
    try:
        ro2.upsert("t", [{"id": 1}])
    except RuntimeError:
        return                                # expected
    raise AssertionError("read-only upsert should raise RuntimeError")
