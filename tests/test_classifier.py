"""Trend classifier unit tests.

Convention: `_ts(N)` returns `now - N days`. So `[ _ts(6-i) for i in range(7) ]`
gives timestamps in chronological ASCENDING order when iterated in order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trend_hunter.intelligence.classifier import Status, classify_one


def _ts(days_ago: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days_ago)


def test_classify_empty():
    out = classify_one([])
    assert out is None


def test_classify_single_observation_is_unknown():
    out = classify_one([("s", "e", _ts(1), 10.0)])
    assert out is not None
    assert out.status == Status.UNKNOWN
    assert out.n_points == 1


def test_classify_rising():
    # Chronological ASC: 6 days ago → today; prices 10.0 → 19.0 = RISING.
    history = [("s", "e", _ts(6 - i), 10.0 + 1.5 * i) for i in range(7)]
    out = classify_one(history)
    assert out is not None
    assert out.status == Status.RISING
    assert out.slope_pct_per_day > 0


def test_classify_declining():
    # Chronological ASC: 6 days ago → today; prices 100.0 → 88.0 = DECLINING.
    history = [("s", "e", _ts(6 - i), 100.0 - 2.0 * i) for i in range(7)]
    out = classify_one(history)
    assert out is not None
    assert out.status == Status.DECLINING
    assert out.slope_pct_per_day < 0


def test_classify_stable():
    # Chronological ASC: small drift, well under threshold.
    history = [("s", "e", _ts(6 - i), 50.0 + 0.001 * i) for i in range(7)]
    out = classify_one(history)
    assert out is not None
    assert out.status == Status.STABLE


def test_classify_filters_old_history():
    """Points outside the 7-day window must be excluded from the slope fit.

    Chronological ASC:
      fresh = prices 10.0 → 8.2 (DECLINING, in window)
      stale = big swing outside window — must NOT flip the verdict
    """
    fresh = [("s", "e", _ts(6 - i), 10.0 - 0.3 * i) for i in range(7)]
    stale = [("s", "e", _ts(40 - j), 100.0 + 5.0 * j) for j in range(7)]
    out = classify_one(fresh + stale)
    assert out is not None
    assert out.status == Status.DECLINING
    # the classifier should only see 7 points (the fresh series)
    assert out.n_points == 7
