"""Tests for cross-timeframe pattern-change alerting.

Covers transition detection, idempotency, and persistence in
cross_timeframe_patterns.  Alerts surface directly in the dashboard
(no external messaging), so tests verify the database state and
returned summary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.observe.pattern_alerts import check_pattern_alerts


# ── helpers ───────────────────────────────────────────────────────────────────
def _insert_trend_history(
    storage: DuckDBStorage,
    source: str,
    external_id: str,
    status: str,
    slope: float,
    window_end: datetime,
    timeframe_days: int = 1,
) -> None:
    """Insert a single trend_history snapshot for a product/timeframe."""
    storage.execute(
        """
        INSERT INTO trend_history (
            source, external_id, title, status, slope_pct_per_day,
            n_points, window_start, window_end, timeframe_days
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (
            source,
            external_id,
            f"Product {external_id}",
            status,
            slope,
            5,
            window_end - timedelta(days=timeframe_days),
            window_end,
            timeframe_days,
        ),
    )


def _breakout_snapshots(
    storage: DuckDBStorage,
    source: str,
    external_id: str,
    window_end: datetime,
) -> None:
    """Insert snapshots that produce a cross-timeframe BREAKOUT pattern.

    Breakout: shortest window rising, longer windows stable/unknown.
    """
    _insert_trend_history(storage, source, external_id, "stable", 0.0, window_end, 90)
    _insert_trend_history(storage, source, external_id, "stable", 0.0, window_end, 30)
    _insert_trend_history(storage, source, external_id, "stable", 0.0, window_end, 14)
    _insert_trend_history(storage, source, external_id, "stable", 0.0, window_end, 7)
    _insert_trend_history(storage, source, external_id, "rising", 0.05, window_end, 1)


def _accelerating_snapshots(
    storage: DuckDBStorage,
    source: str,
    external_id: str,
    window_end: datetime,
) -> None:
    """Insert snapshots that produce a cross-timeframe ACCELERATING pattern."""
    _insert_trend_history(storage, source, external_id, "rising", 0.01, window_end, 90)
    _insert_trend_history(storage, source, external_id, "rising", 0.02, window_end, 30)
    _insert_trend_history(storage, source, external_id, "rising", 0.03, window_end, 14)
    _insert_trend_history(storage, source, external_id, "rising", 0.04, window_end, 7)
    _insert_trend_history(storage, source, external_id, "rising", 0.05, window_end, 1)


def _steady_snapshots(
    storage: DuckDBStorage,
    source: str,
    external_id: str,
    window_end: datetime,
) -> None:
    """Insert snapshots that produce a cross-timeframe STEADY pattern."""
    _insert_trend_history(storage, source, external_id, "stable", 0.0, window_end, 90)
    _insert_trend_history(storage, source, external_id, "stable", 0.0, window_end, 30)
    _insert_trend_history(storage, source, external_id, "stable", 0.0, window_end, 14)
    _insert_trend_history(storage, source, external_id, "stable", 0.0, window_end, 7)
    _insert_trend_history(storage, source, external_id, "stable", 0.0, window_end, 1)


# ── transition detection ───────────────────────────────────────────────────────

def test_no_alert_when_no_trend_history(storage):
    """With empty trend_history, check returns zero alerts."""
    summary = check_pattern_alerts(storage)
    assert summary == {"checked": 0, "alerted": 0, "patterns": []}


def test_alert_on_first_breakout(storage):
    """A product with no previous pattern triggers an alert on breakout."""
    now = datetime.now(UTC)
    _breakout_snapshots(storage, "shopify", "sku-1", now)

    summary = check_pattern_alerts(storage)

    assert summary["checked"] == 1
    assert summary["alerted"] == 1
    assert summary["patterns"][0]["pattern"] == "breakout"

    # Verify DB was updated.
    rows = storage.query(
        "SELECT alerted_at FROM cross_timeframe_patterns WHERE external_id = ?",
        ("sku-1",),
    )
    assert len(rows) == 1
    assert rows[0]["alerted_at"] is not None


def test_no_alert_when_pattern_unchanged(storage):
    """Re-running with the same pattern does not alert again."""
    now = datetime.now(UTC)
    _breakout_snapshots(storage, "shopify", "sku-1", now)

    summary1 = check_pattern_alerts(storage)
    assert summary1["alerted"] == 1

    # Second run with identical data should not re-alert.
    summary2 = check_pattern_alerts(storage)
    assert summary2["alerted"] == 0


def test_alert_on_transition_to_accelerating(storage):
    """A transition from steady to accelerating triggers an alert."""
    now = datetime.now(UTC)
    _steady_snapshots(storage, "shopify", "sku-2", now)

    # First run — steady, no alert.
    summary1 = check_pattern_alerts(storage)
    assert summary1["alerted"] == 0

    # Now overwrite with accelerating snapshots for the same product.
    _accelerating_snapshots(storage, "shopify", "sku-2", now + timedelta(hours=1))
    summary2 = check_pattern_alerts(storage)

    assert summary2["alerted"] == 1
    assert summary2["patterns"][0]["pattern"] == "accelerating"


def test_only_breakout_and_accelerating_alert(storage):
    """Steady, declining, and other patterns do not trigger alerts."""
    now = datetime.now(UTC)
    _steady_snapshots(storage, "shopify", "sku-3", now)

    summary = check_pattern_alerts(storage)

    assert summary["alerted"] == 0


# ── persistence ───────────────────────────────────────────────────────────────

def test_previous_patterns_are_persisted(storage):
    """The cross_timeframe_patterns table records the latest pattern."""
    now = datetime.now(UTC)
    _breakout_snapshots(storage, "shopify", "sku-4", now)

    check_pattern_alerts(storage)

    rows = storage.query(
        "SELECT source, external_id, pattern FROM cross_timeframe_patterns"
    )
    assert len(rows) == 1
    assert rows[0]["pattern"] == "breakout"


def test_alerted_at_is_set_on_first_alert(storage):
    """alerted_at is populated when a pattern-change alert is detected."""
    now = datetime.now(UTC)
    _breakout_snapshots(storage, "shopify", "sku-5", now)

    check_pattern_alerts(storage)

    rows = storage.query(
        "SELECT alerted_at FROM cross_timeframe_patterns WHERE external_id = ?",
        ("sku-5",),
    )
    assert len(rows) == 1
    assert rows[0]["alerted_at"] is not None


def test_summary_includes_product_details(storage):
    """The returned alert summary includes pattern, title, source, and external_id."""
    now = datetime.now(UTC)
    _breakout_snapshots(storage, "shopify", "sku-6", now)

    summary = check_pattern_alerts(storage)

    assert summary["alerted"] == 1
    pattern_info = summary["patterns"][0]
    assert pattern_info["pattern"] == "breakout"
    assert "Product sku-6" in pattern_info["title"]
    assert pattern_info["source"] == "shopify"
    assert pattern_info["external_id"] == "sku-6"
