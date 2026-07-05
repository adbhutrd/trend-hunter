"""Tests for the calibrate module — forecast back-testing + MAPE.

Uses the shared ``storage`` fixture.
"""

from __future__ import annotations

import pytest

from trend_hunter.observe.calibrate import (
    auto_resolve_calibrations,
    calibration_summary,
    pending_calibrations,
    record_calibration,
    update_actual,
)


def test_record_calibration_returns_id(storage):
    """record_calibration inserts a row and returns the calibrate_id."""
    cid = record_calibration(
        storage,
        sku="sku-001",
        predicted_price=14.50,
        horizon_days=14,
        source_command="forecast",
    )
    assert isinstance(cid, str)
    assert len(cid) > 0


def test_record_calibration_persists_row(storage):
    """The inserted row is queryable from the calibrate table."""
    cid = record_calibration(
        storage,
        sku="sku-002",
        predicted_price=20.00,
        horizon_days=7,
        source_command="scan",
    )
    rows = storage.query(
        "SELECT calibrate_id, sku, predicted_price, horizon_days, source_command "
        "FROM calibrate WHERE calibrate_id = ?",
        (cid,),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["calibrate_id"] == cid
    assert row["sku"] == "sku-002"
    assert float(row["predicted_price"]) == 20.00
    assert row["source_command"] == "scan"


def test_record_calibration_actual_is_null_by_default(storage):
    """When actual_price is not given, it stays NULL."""
    cid = record_calibration(
        storage,
        sku="sku-003",
        predicted_price=10.00,
        horizon_days=14,
    )
    rows = storage.query(
        "SELECT actual_price FROM calibrate WHERE calibrate_id = ?",
        (cid,),
    )
    assert rows[0]["actual_price"] is None


def test_update_actual_sets_price_and_pct(storage):
    """update_actual fills actual_price and computes error_pct."""
    cid = record_calibration(
        storage,
        sku="sku-004",
        predicted_price=10.00,
        horizon_days=14,
    )
    update_actual(storage, cid, 12.00)
    rows = storage.query(
        "SELECT actual_price, error_pct FROM calibrate WHERE calibrate_id = ?",
        (cid,),
    )
    assert float(rows[0]["actual_price"]) == 12.00
    # error_pct = (12 - 10) / 10 = 0.20
    assert float(rows[0]["error_pct"]) == pytest.approx(0.20)


def test_update_actual_nonexistent_is_noop(storage):
    """update_actual on a missing calibrate_id should not crash."""
    update_actual(storage, "nonexistent-id", 99.99)  # no-op, no crash


def test_pending_calibrations_returns_unresolved(storage):
    """pending_calibrations returns rows where actual_price IS NULL."""
    cid = record_calibration(
        storage,
        sku="sku-pending",
        predicted_price=15.00,
        horizon_days=7,
    )
    # Record one with actual set
    record_calibration(
        storage,
        sku="sku-resolved",
        predicted_price=10.00,
        actual_price=11.00,
        horizon_days=7,
    )
    pending = pending_calibrations(storage, days=30)
    ids = [r["calibrate_id"] for r in pending]
    assert cid in ids


def test_calibration_summary_empty(storage):
    """calibration_summary returns zero count when no row exists."""
    summary = calibration_summary(storage, days=7)
    assert summary["count"] == 0
    assert summary["mape_pct"] is None


def test_calibration_summary_computes_mape(storage):
    """calibration_summary computes MAPE from resolved records."""
    cid1 = record_calibration(storage, sku="s1", predicted_price=10.00)
    update_actual(storage, cid1, 12.00)  # error_pct = 0.20

    cid2 = record_calibration(storage, sku="s2", predicted_price=20.00)
    update_actual(storage, cid2, 18.00)  # error_pct = -0.10

    summary = calibration_summary(storage, days=7)
    assert summary["count"] == 2
    # MAPE = (|0.20| + |-0.10|) / 2 * 100 = (0.20 + 0.10) / 2 * 100 = 15.0%
    assert summary["mape_pct"] == pytest.approx(15.0)


# ── auto_resolve_calibrations — the forecast back-test leg ────────────────────
def test_auto_resolve_picks_up_overdue(storage):
    """A backdated overdue forecast with a matching product resolves."""
    # Backdate by 15 days; horizon default = 14 → overdue.
    storage.execute(
        "INSERT INTO calibrate "
        "(calibrate_id, sku, ts_forecast, predicted_price, horizon_days, source_command) "
        "VALUES ('cid-1', 'sku-A', current_timestamp - INTERVAL 15 DAY, "
        "20.00, 14, 'forecast')",
    )
    # A fresh product observation matching the SKU.
    storage.execute(
        "INSERT INTO products (source, external_id, captured_at, price) "
        "VALUES ('shopify', 'sku-A', current_timestamp, 24.00)",
    )
    out = auto_resolve_calibrations(storage, days_lookback=30)
    assert out == {"resolved": 1, "skipped": 0, "total": 1}
    row = storage.query(
        "SELECT actual_price, error_pct FROM calibrate WHERE calibrate_id = 'cid-1'",
    )[0]
    assert float(row["actual_price"]) == pytest.approx(24.00)
    # error_pct = (24 - 20) / 20 = 0.20
    assert float(row["error_pct"]) == pytest.approx(0.20)


def test_auto_resolve_idempotent(storage):
    """Re-running picks up zero — already-resolved rows are skipped by SQL."""
    storage.execute(
        "INSERT INTO calibrate "
        "(calibrate_id, sku, ts_forecast, predicted_price, horizon_days) "
        "VALUES ('cid-i', 'sku-B', current_timestamp - INTERVAL 15 DAY, "
        "10.00, 14)",
    )
    storage.execute(
        "INSERT INTO products (source, external_id, captured_at, price) "
        "VALUES ('shopify', 'sku-B', current_timestamp, 12.00)",
    )
    out1 = auto_resolve_calibrations(storage, days_lookback=30)
    out2 = auto_resolve_calibrations(storage, days_lookback=30)
    assert out1["resolved"] == 1
    # The WHERE actual_price IS NULL predicate excludes it the second time.
    assert out2 == {"resolved": 0, "skipped": 0, "total": 0}


def test_auto_resolve_dry_run_does_not_write(storage):
    """dry_run=True reports counts but leaves the row pending."""
    storage.execute(
        "INSERT INTO calibrate "
        "(calibrate_id, sku, ts_forecast, predicted_price, horizon_days) "
        "VALUES ('cid-d', 'sku-D', current_timestamp - INTERVAL 15 DAY, "
        "10.00, 14)",
    )
    storage.execute(
        "INSERT INTO products (source, external_id, captured_at, price) "
        "VALUES ('shopify', 'sku-D', current_timestamp, 12.00)",
    )
    out = auto_resolve_calibrations(storage, days_lookback=30, dry_run=True)
    assert out == {"resolved": 1, "skipped": 0, "total": 1}
    row = storage.query(
        "SELECT actual_price FROM calibrate WHERE calibrate_id = 'cid-d'",
    )[0]
    assert row["actual_price"] is None


def test_auto_resolve_skips_missing_match(storage):
    """Overdue forecast whose SKU has no matching product is skipped, not crashed."""
    storage.execute(
        "INSERT INTO calibrate "
        "(calibrate_id, sku, ts_forecast, predicted_price, horizon_days) "
        "VALUES ('cid-s', 'forecast-smoke', "
        "current_timestamp - INTERVAL 15 DAY, 10.00, 14)",
    )
    out = auto_resolve_calibrations(storage, days_lookback=30)
    assert out == {"resolved": 0, "skipped": 1, "total": 1}
    # Row is still NULL — we don't crash, we just don't claim an actual.
    row = storage.query(
        "SELECT actual_price FROM calibrate WHERE calibrate_id = 'cid-s'",
    )[0]
    assert row["actual_price"] is None


def test_auto_resolve_ignores_pre_horizon_rows(storage):
    """Forecasts that haven't reached their horizon yet are not touched."""
    storage.execute(
        "INSERT INTO calibrate "
        "(calibrate_id, sku, ts_forecast, predicted_price, horizon_days) "
        "VALUES ('cid-future', 'sku-Z', current_timestamp, 10.00, 14)",
    )
    storage.execute(
        "INSERT INTO products (source, external_id, captured_at, price) "
        "VALUES ('shopify', 'sku-Z', current_timestamp, 11.00)",
    )
    out = auto_resolve_calibrations(storage, days_lookback=30)
    assert out == {"resolved": 0, "skipped": 0, "total": 0}
