"""Tests for trend_hunter.intelligence.patterns.

The ``detect_pattern`` function is pure logic: feed it ordered classification
snapshots and assert the labelled pattern.  ``detect_multi_timeframe_pattern``
works across the 1d/7d/14d/30d/90d snapshots for a single product.
"""

from __future__ import annotations

from trend_hunter.intelligence.patterns import (
    Pattern,
    detect_multi_timeframe_pattern,
    detect_pattern,
)


def _snap(status: str, slope: float, window_end: str = "2026-01-01") -> dict:
    return {
        "status": status,
        "slope_pct_per_day": slope,
        "window_end": window_end,
    }


def test_empty_history_returns_none():
    assert detect_pattern([]) is None


def test_single_snapshot_returns_none():
    assert detect_pattern([_snap("rising", 0.05)]) is None


def test_breakout_rising_after_stable():
    history = [
        _snap("stable", 0.01),
        _snap("rising", 0.08),
    ]
    assert detect_pattern(history) == Pattern.BREAKOUT


def test_cooling_stable_after_rising():
    history = [
        _snap("rising", 0.08),
        _snap("stable", 0.01),
    ]
    assert detect_pattern(history) == Pattern.COOLING


def test_accelerating_when_slope_increases():
    history = [
        _snap("rising", 0.05),
        _snap("rising", 0.10),
    ]
    assert detect_pattern(history) == Pattern.ACCELERATING


def test_sustained_growth_when_slope_flat():
    history = [
        _snap("rising", 0.05),
        _snap("rising", 0.052),
    ]
    assert detect_pattern(history) == Pattern.SUSTAINED_GROWTH


def test_rebounding_rising_after_declining():
    history = [
        _snap("declining", -0.03),
        _snap("rising", 0.04),
    ]
    assert detect_pattern(history) == Pattern.REBOUNDING


def test_peaking_declining_after_rising():
    history = [
        _snap("rising", 0.08),
        _snap("declining", -0.02),
    ]
    assert detect_pattern(history) == Pattern.PEAKING


def test_declining_after_stable():
    history = [
        _snap("stable", 0.01),
        _snap("declining", -0.02),
    ]
    assert detect_pattern(history) == Pattern.PEAKING


def test_steady_stays_stable():
    history = [
        _snap("stable", 0.01),
        _snap("stable", 0.01),
    ]
    assert detect_pattern(history) == Pattern.STEADY


def test_unknown_when_data_missing():
    history = [
        _snap("stable", 0.01),
        _snap("unknown", 0.0),
    ]
    assert detect_pattern(history) == Pattern.UNKNOWN


# ── multi-timeframe tests ───────────────────────────────────────────────────

def test_multi_accelerating():
    # Longest to shortest: slope increases toward the most recent window.
    snapshots = [
        _snap("rising", 0.01),  # 90d
        _snap("rising", 0.02),  # 30d
        _snap("rising", 0.05),  # 14d
        _snap("rising", 0.10),  # 7d
        _snap("rising", 0.20),  # 1d
    ]
    assert detect_multi_timeframe_pattern(snapshots) == Pattern.ACCELERATING


def test_multi_sustained_growth():
    snapshots = [_snap("rising", 0.05) for _ in range(5)]
    assert detect_multi_timeframe_pattern(snapshots) == Pattern.SUSTAINED_GROWTH


def test_multi_peaking():
    snapshots = [
        _snap("rising", 0.10),  # 90d
        _snap("rising", 0.10),  # 30d
        _snap("rising", 0.08),  # 14d
        _snap("rising", 0.06),  # 7d
        _snap("declining", -0.02),  # 1d
    ]
    assert detect_multi_timeframe_pattern(snapshots) == Pattern.PEAKING


def test_multi_rebounding():
    snapshots = [
        _snap("declining", -0.05),  # 90d
        _snap("declining", -0.04),  # 30d
        _snap("declining", -0.03),  # 14d
        _snap("declining", -0.02),  # 7d
        _snap("rising", 0.08),  # 1d
    ]
    assert detect_multi_timeframe_pattern(snapshots) == Pattern.REBOUNDING


def test_multi_breakout():
    snapshots = [
        _snap("stable", 0.0),  # 90d
        _snap("stable", 0.0),  # 30d
        _snap("stable", 0.0),  # 14d
        _snap("stable", 0.0),  # 7d
        _snap("rising", 0.10),  # 1d
    ]
    assert detect_multi_timeframe_pattern(snapshots) == Pattern.BREAKOUT


def test_multi_declining():
    snapshots = [_snap("declining", -0.05) for _ in range(5)]
    assert detect_multi_timeframe_pattern(snapshots) == Pattern.DECLINING


def test_multi_steady():
    snapshots = [_snap("stable", 0.01) for _ in range(5)]
    assert detect_multi_timeframe_pattern(snapshots) == Pattern.STEADY
