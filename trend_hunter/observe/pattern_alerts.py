"""Cross-timeframe pattern-change alerts.

Detects products whose cross-timeframe pattern has just changed to
``Pattern.BREAKOUT`` or ``Pattern.ACCELERATING``.  Previous patterns are
persisted in ``cross_timeframe_patterns`` so re-runs are idempotent and
transitions are tracked over time.  The dashboard reads this table to
surface recent alerts directly — no external messaging required.

Usage::

    from trend_hunter.observe.pattern_alerts import check_pattern_alerts

    new_alerts = check_pattern_alerts(storage)
"""

from __future__ import annotations

from typing import Any

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.intelligence.patterns import (
    Pattern,
    build_cross_timeframe_snapshots,
    detect_multi_timeframe_pattern,
)

# Patterns that should trigger an alert when they appear as a new
# cross-timeframe state (or transition from a different pattern).
_ALERT_PATTERNS = {Pattern.BREAKOUT, Pattern.ACCELERATING}


def _load_previous_patterns(storage: DuckDBStorage) -> dict[tuple[str, str], dict[str, Any]]:
    """Return the most recently recorded cross-timeframe pattern per product."""
    rows = storage.query(
        """
        SELECT source, external_id, title, pattern, status,
               slope_pct_per_day, window_end, detected_at, alerted_at
        FROM cross_timeframe_patterns
        """,
    )
    return {
        (str(r["source"]), str(r["external_id"])): r
        for r in rows
    }


def _compute_current_patterns(
    storage: DuckDBStorage,
) -> list[dict[str, Any]]:
    """Read trend_history and compute the current cross-timeframe pattern for each product.

    Returns a list of dicts with keys: source, external_id, title, pattern,
    status, slope_pct_per_day, window_end.
    """
    rows = storage.query(
        """
        SELECT source, external_id, title, status, slope_pct_per_day,
               n_points, window_start, window_end, timeframe_days
        FROM trend_history
        ORDER BY source, external_id, timeframe_days, window_end ASC
        """,
    )
    if not rows:
        return []

    current: list[dict[str, Any]] = []
    for snapshots in build_cross_timeframe_snapshots(rows):
        pattern = detect_multi_timeframe_pattern(snapshots)
        if pattern is None:
            continue
        latest = snapshots[-1]
        current.append(
            {
                "source": str(latest["source"]),
                "external_id": str(latest["external_id"]),
                "title": latest.get("title") or latest["external_id"],
                "pattern": pattern,
                "status": latest.get("status", "unknown"),
                "slope_pct_per_day": latest.get("slope_pct_per_day", 0.0),
                "window_end": latest["window_end"],
            }
        )
    return current


def _find_changes(
    current: list[dict[str, Any]],
    previous: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return products whose current pattern is alert-worthy and changed from previous.

    A product alerts when:
    * its current cross-timeframe pattern is BREAKOUT or ACCELERATING, AND
    * the previous recorded pattern for the same product is different or missing.
    """
    changes: list[dict[str, Any]] = []
    for row in current:
        pattern = row["pattern"]
        if pattern not in _ALERT_PATTERNS:
            continue
        key = (row["source"], row["external_id"])
        prev = previous.get(key)
        if prev is None or Pattern(prev["pattern"]) != pattern:
            changes.append(row)
    return changes


def _persist_patterns(storage: DuckDBStorage, patterns: list[dict[str, Any]]) -> None:
    """Persist the current cross-timeframe patterns into the ledger.

    Uses DELETE + INSERT to avoid DuckDB index corruption with
    ON CONFLICT on compound-unique constraints.
    """
    if not patterns:
        return
    c = storage.conn()
    c.execute("BEGIN")
    try:
        # Clear old patterns for the same products
        for row in patterns:
            c.execute(
                "DELETE FROM cross_timeframe_patterns WHERE source = ? AND external_id = ?",
                (row["source"], row["external_id"]),
            )
        # Insert fresh
        for row in patterns:
            c.execute(
                """
                INSERT INTO cross_timeframe_patterns (
                    source, external_id, title, pattern, status,
                    slope_pct_per_day, window_end, detected_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, current_timestamp)
                """,
                (
                    row["source"],
                    row["external_id"],
                    row["title"],
                    str(row["pattern"]),
                    row["status"],
                    row["slope_pct_per_day"],
                    row["window_end"],
                ),
            )
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def _mark_alerted(storage: DuckDBStorage, keys: list[tuple[str, str]]) -> None:
    """Set alerted_at = now for the given products."""
    if not keys:
        return
    c = storage.conn()
    c.execute("BEGIN")
    try:
        for source, external_id in keys:
            c.execute(
                """
                UPDATE cross_timeframe_patterns
                SET alerted_at = current_timestamp
                WHERE source = ? AND external_id = ?
                """,
                (source, external_id),
            )
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def check_pattern_alerts(storage: DuckDBStorage) -> dict[str, Any]:
    """Check for cross-timeframe pattern transitions and alert if configured.

    Parameters
    ----------
    storage:
        A writer ``DuckDBStorage`` instance.

    Returns
    -------
    dict with keys ``checked`` (number of products checked), ``alerted``
    (number of alerts sent), and ``patterns`` (list of alerted products).
    """
    previous = _load_previous_patterns(storage)
    current = _compute_current_patterns(storage)

    # Persist all current patterns first so the ledger is always up to date.
    _persist_patterns(storage, current)

    changes = _find_changes(current, previous)
    if not changes:
        return {"checked": len(current), "alerted": 0, "patterns": []}

    # Store the alert in cross_timeframe_patterns so the dashboard surfaces it.
    _mark_alerted(storage, [(c["source"], c["external_id"]) for c in changes])

    return {
        "checked": len(current),
        "alerted": len(changes),
        "patterns": [
            {
                "source": c["source"],
                "external_id": c["external_id"],
                "title": c["title"],
                "pattern": str(c["pattern"]),
            }
            for c in changes
        ],
    }
