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
        )
        / len(rows),
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


# ── auto-resolve (forecast back-test leg of the loop) ─────────────────────────
def auto_resolve_calibrations(
    storage: Any,
    *,
    days_lookback: int = 30,
    dry_run: bool = False,
) -> dict[str, int]:
    """Resolve overdue pending calibrations against the ``products`` table.

    For every ``calibrate`` row that has passed its horizon
    (``ts_forecast + horizon_days <= now()``) and still has no
    ``actual_price``, look up the most recent ``products.price`` whose
    ``external_id`` matches the SKU *and* was captured at-or-after the
    forecast timestamp.  If found, write it via :func:`update_actual` so
    ``error_pct`` and ``ts_actual`` are populated and ``calibration_summary``
    picks it up on the next ``loop-report``.  Missing price data is skipped
    (never crashes the loop).

    Idempotent: re-running on already-resolved rows is a no-op because
    ``actual_price IS NULL`` excludes them.  ``dry_run=True`` reports
    counts without writing.

    Parameters
    ----------
    storage:
        Any object with ``.query(sql, params)`` and ``.execute(sql, params)``
        methods (i.e. anything implementing the ``Storage`` protocol).
    days_lookback:
        Only consider calibrations logged within the last N days (cheap
        safety so a stale row from years ago never quietly re-resolves).
    dry_run:
        If True, do not write — return counts only.

    Returns
    -------
    ``{"resolved": int, "skipped": int, "total": int}``
    """
    pending_rows = storage.query(
        f"""
        SELECT calibrate_id, sku, ts_forecast
        FROM calibrate
        WHERE actual_price IS NULL
          AND ts_forecast + INTERVAL 1 DAY * horizon_days <= current_timestamp
          AND ts_forecast >= current_timestamp - INTERVAL {int(days_lookback)} DAY
        """,
    )

    resolved = 0
    skipped = 0
    # Note: an N+1 query pattern is intentional here.  ``calibrate`` grows at
    # one row per forecast — typically tiny per cycle (10s, not 1000s) — and
    # ``ts_forecast`` is per-row, so a single UPDATE…FROM join would require
    # a window-function over the products table or a correlated subquery
    # that's both harder to read and slower at this scale.  Keep the loop;
    # revisit if calibrate ever grows past ~hundreds per cycle.
    for row in pending_rows:
        sku = row["sku"]
        ts_forecast = row["ts_forecast"]
        cid = row["calibrate_id"]
        # Match calibrate.sku ↔ products.external_id.  Require the observation
        # to be at/after the forecast timestamp so we never claim a price that
        # was captured *before* the prediction was logged.
        price_rows = storage.query(
            """
            SELECT price
            FROM products
            WHERE external_id = ?
              AND captured_at >= ?
              AND price IS NOT NULL
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (sku, ts_forecast),
        )
        if not price_rows:
            skipped += 1
            continue
        if not dry_run:
            update_actual(storage, cid, float(price_rows[0]["price"]))
        resolved += 1
    return {"resolved": resolved, "skipped": skipped, "total": len(pending_rows)}
