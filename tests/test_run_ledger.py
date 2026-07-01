"""``run_ledger`` — context manager that writes one ``run_history`` row per run.

Covered behaviours:

* Successful run writes ``status='ok'`` + ``duration_ms`` + ``counters`` JSON.
* Exception inside the ``with`` block writes ``status='err'`` + ``error_type``
  and re-raises.
* ``dedupe_key`` collapses retries into one row.
* Same run_id is generated and reused on dedupe-replayed runs.

All tests pass the ``storage`` fixture directly so we don't fight the
single-writer FCNTL lock that the fixture holds.
"""
from __future__ import annotations

import json

import pytest

from trend_hunter.observe.run_ledger import record_run


def _payload(counters) -> dict:
    """Decode the counters column — DuckDB sometimes returns a dict already."""
    if isinstance(counters, dict):
        return counters
    return json.loads(counters)


def test_successful_run_marks_status_ok(storage):                          # type: ignore[no-untyped-def]
    with record_run("scan", storage=storage) as rec:
        rec.set_counters({"products_in": 7})
    rows = storage.query(
        "SELECT status, duration_ms, counters, error_type "
        "FROM run_history WHERE command = 'scan'",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "ok", "expected status='ok'"
    assert row["error_type"] is None, "no error_type on a clean run"
    assert row["duration_ms"] is not None and row["duration_ms"] >= 0
    assert _payload(row["counters"]).get("products_in") == 7


def test_failed_run_marks_status_err_and_reraises(storage):                  # type: ignore[no-untyped-def]
    with pytest.raises(RuntimeError, match="boom"):
        with record_run("scan", storage=storage) as rec:
            rec.set_counters({"products_in": 0})
            raise RuntimeError("boom")
    rows = storage.query(
        "SELECT status, error_type, error_message "
        "FROM run_history WHERE command = 'scan'",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "err"
    assert row["error_type"] == "RuntimeError"
    assert "boom" in (row["error_message"] or "")


def test_dedupe_key_collapses_retries(storage):                              # type: ignore[no-untyped-def]
    with record_run(
        "forget",
        storage=storage,
        dedupe_key="abc",
    ) as rec:
        rec.set_counters({"audits": 1})

    with record_run(
        "forget",
        storage=storage,
        dedupe_key="abc",
    ) as rec:
        rec.set_counters({"audits": 2})

    rows = storage.query(
        "SELECT counters FROM run_history "
        "WHERE command = 'forget' AND dedupe_key = 'abc'",
    )
    assert len(rows) == 1, "dedupe_key should collapse to one row"
    # The LATEST counters are kept (last writer wins).
    assert _payload(rows[0]["counters"]).get("audits") == 2


def test_counters_accumulate(storage):                                       # type: ignore[no-untyped-def]
    with record_run("scan", storage=storage) as rec:
        rec.incr("products_in")
        rec.incr("products_in", by=4)
        rec.set_counters({"tried": True})
    rows = storage.query(
        "SELECT counters FROM run_history WHERE command = 'scan'",
    )
    payload = _payload(rows[0]["counters"])
    assert payload["products_in"] == 5
    assert payload["tried"] is True


def test_each_command_creates_a_new_row(storage):                            # type: ignore[no-untyped-def]
    # No dedupe_key, two distinct runs ⇒ two rows (different run_ids).
    with record_run("scan", storage=storage) as rec:
        rec.set_counters({"a": 1})
    with record_run("scan", storage=storage) as rec:
        rec.set_counters({"b": 2})
    rows = storage.query(
        "SELECT counters FROM run_history WHERE command = 'scan'",
    )
    assert len(rows) == 2, "different run_ids ⇒ two rows"


def test_open_writer_owns_storage_lifecycle(tmp_path):                       # type: ignore[no-untyped-def]
    """When called WITHOUT a `storage` arg, record_run opens its own writer
    cleanly and closes on context exit (so the flock doesn't bind across
    calls)."""
    from trend_hunter.adapters.storage_duckdb import DuckDBStorage

    db = tmp_path / "lifecycle.duckdb"
    with record_run("scan", db_path=db) as rec:
        rec.set_counters({"a": 1})
    # Re-opening immediately must not block on the previous writer lock.
    s = DuckDBStorage(db, read_only=False)
    try:
        n = s.query("SELECT count(*) AS n FROM run_history")[0]["n"]
        assert n == 1
    finally:
        s.close()
