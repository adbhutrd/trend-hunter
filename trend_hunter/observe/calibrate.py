"""Calibrate — forecast back-testing table + MAPE computation.

The "compare" leg of the loop-engineering spine.  Every forecast prediction
lands in the ``calibrate`` table alongside the eventual actual price so the
system can compute MAPE (Mean Absolute Percentage Error) and surface it in
``loop-report``.

Usage::

    from trend_hunter.observe.calibrate import record_calibration

    record_calibration(storage, sku="ABC-123", predicted_price=14.50,
                       horizon_days=14, source_command="forecast")
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


# ── write-side ─────────────────────────────────────────────────────────────────
def record_calibration(
    storage: Any,
    *,
    sku: str,
    predicted_price: float,
    actual_price: float | None = None,
    horizon_days: int = 14,
    source_command: str | None = None,
) -> str:
    """Insert a calibration row.  Returns the ``calibrate_id``.

    Call this right after making a forecast.  When the actual price becomes
    known (e.g. after a scaffold push records a real sale), update via
    ``update_actual()``.
    """
    calibrate_id = str(uuid.uuid4())
    storage.execute(
        "INSERT INTO calibrate "
        "(calibrate_id, sku, ts_forecast, predicted_price, actual_price, "
        " error_pct, horizon_days, source_command) "
        "VALUES (?, ?, current_timestamp, ?, ?, NULL, ?, ?)",
        (calibrate_id, sku, predicted_price, actual_price, horizon_days, source_command),
    )
    return calibrate_id


def update_actual(storage: Any, calibrate_id: str, actual_price: float) -> None:
    """Update a calibration row with the realised price + compute error_pct."""
    predicted = storage.query(
        "SELECT predicted_price FROM calibrate WHERE calibrate_id = ?",
        (calibrate_id,),
    )
    if not predicted:
        return
    predicted_price = float(predicted[0]["predicted_price"])
    error_pct = (actual_price - predicted_price) / predicted_price if predicted_price else 0.0
    storage.execute(
        "UPDATE calibrate SET actual_price = ?, error_pct = ?, "
        "ts_actual = current_timestamp WHERE calibrate_id = ?",
        (actual_price, error_pct, calibrate_id),
    )


# ── read-side helpers ──────────────────────────────────────────────────────────
def calibration_summary(storage: Any, days: int = 7) -> dict:
    """Return MAPE + count + mean abs error for recent calibration records."""
    rows = storage.query(
        f"""
        SELECT error_pct, predicted_price, actual_price
        FROM calibrate
        WHERE actual_price IS NOT NULL
          AND ts_forecast >= now() - INTERVAL {int(days)} DAY
        """,
    )
    if not rows:
        return {"count": 0, "mape_pct": None, "mean_abs_error": None}

    abs_errors = []
    for r in rows:
        err = float(r["error_pct"]) if r["error_pct"] is not None else None
        if err is not None:
            abs_errors.append(abs(err))

    if not abs_errors:
        return {"count": len(rows), "mape_pct": None, "mean_abs_error": None}

    return {
        "count": len(abs_errors),
        "mape_pct": (sum(abs_errors) / len(abs_errors)) * 100,
        "mean_abs_error": sum(
            abs(float(r["actual_price"]) - float(r["predicted_price"]))
            for r in rows
            if r["actual_price"] is not None and r["predicted_price"] is not None
        ) / len(rows),
    }


def pending_calibrations(storage: Any, days: int = 30) -> list[dict]:
    """Calibration rows without an actual yet — predictions awaiting feedback."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return storage.query(
        "SELECT calibrate_id, sku, predicted_price, ts_forecast, "
        "horizon_days, source_command "
        "FROM calibrate "
        "WHERE actual_price IS NULL AND ts_forecast >= ? "
        "ORDER BY ts_forecast DESC",
        (cutoff,),
    )
