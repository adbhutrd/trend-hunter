"""Historical trend pattern detection.

Given an ordered list of classification snapshots for a single product,
detect higher-level patterns like breakouts, cooling, acceleration,
rebounds, and peaking.  Snapshots are expected to be sorted by
``window_end`` ascending (oldest first).

This module is pure logic — no I/O — so it is trivial to unit-test.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Pattern(StrEnum):
    BREAKOUT = "breakout"
    COOLING = "cooling"
    ACCELERATING = "accelerating"
    SUSTAINED_GROWTH = "sustained growth"
    REBOUNDING = "rebounding"
    PEAKING = "peaking"
    DECLINING = "declining"
    STEADY = "steady"
    UNKNOWN = "unknown"


def _latest_status(history: list[dict[str, Any]]) -> str:
    return history[-1].get("status", "unknown") if history else "unknown"


def _slope(history: list[dict[str, Any]], index: int = -1) -> float:
    try:
        return float(history[index].get("slope_pct_per_day", 0.0) or 0.0)
    except IndexError:
        return 0.0


def detect_pattern(history: list[dict[str, Any]]) -> Pattern | None:
    """Detect a multi-period trend pattern from a product's classification history.

    Parameters
    ----------
    history:
        List of classification rows for a single ``(source, external_id)``,
        sorted by ``window_end`` ascending.  Each row must have at least
        ``status`` and ``slope_pct_per_day`` keys.

    Returns
    -------
    A ``Pattern`` enum value, or ``None`` if no clear pattern is detected.
    """
    if len(history) < 2:
        return None

    prev = history[-2]
    last = history[-1]

    prev_status = prev.get("status", "unknown")
    last_status = last.get("status", "unknown")
    prev_slope = _slope(history, -2)
    last_slope = _slope(history, -1)

    # Rising after stable / unknown → new trend starting.
    if last_status == "rising" and prev_status in ("stable", "unknown"):
        return Pattern.BREAKOUT

    # Stable after rising → momentum is cooling off.
    if last_status == "stable" and prev_status == "rising":
        return Pattern.COOLING

    # Two consecutive rising snapshots: is it speeding up or steady?
    if last_status == "rising" and prev_status == "rising":
        if last_slope > prev_slope * 1.2:
            return Pattern.ACCELERATING
        return Pattern.SUSTAINED_GROWTH

    # Rising immediately after declining → recovery.
    if last_status == "rising" and prev_status == "declining":
        return Pattern.REBOUNDING

    # Declining after a prior rising or stable period → peak passed.
    if last_status == "declining" and prev_status in ("rising", "stable"):
        return Pattern.PEAKING

    # Sustained decline.
    if last_status == "declining" and prev_status == "declining":
        return Pattern.DECLINING

    # Long stable run.
    if last_status == "stable" and prev_status == "stable":
        return Pattern.STEADY

    if last_status == "unknown" or prev_status == "unknown":
        return Pattern.UNKNOWN

    return None


def group_snapshots_by_timeframe(
    snapshots: list[dict[str, Any]],
) -> dict[tuple[str, str, int], list[dict[str, Any]]]:
    """Group trend-history snapshots by (source, external_id, timeframe_days).

    Each group is sorted by ``window_end`` ascending so ``detect_pattern``
    can be applied directly.
    """
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for snap in snapshots:
        key = (
            str(snap["source"]),
            str(snap["external_id"]),
            int(snap["timeframe_days"]),
        )
        groups.setdefault(key, []).append(snap)
    for snaps in groups.values():
        snaps.sort(key=lambda s: s["window_end"])
    return groups


def build_cross_timeframe_snapshots(
    snapshots: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Build per-product cross-timeframe snapshot lists.

    For each product, take the latest snapshot per timeframe and sort the
    resulting list from longest to shortest window.  The returned lists are
    ready for ``detect_multi_timeframe_pattern``.
    """
    by_product: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
    for snap in snapshots:
        source = str(snap["source"])
        ext = str(snap["external_id"])
        tf = int(snap["timeframe_days"])
        key = (source, ext)
        existing = by_product.setdefault(key, {}).get(tf)
        if existing is None or snap["window_end"] > existing["window_end"]:
            by_product[key][tf] = snap

    result: list[list[dict[str, Any]]] = []
    for latest_by_tf in by_product.values():
        if len(latest_by_tf) >= 2:
            snapshots_sorted = sorted(
                latest_by_tf.values(),
                key=lambda s: s["timeframe_days"],
                reverse=True,
            )
            result.append(snapshots_sorted)
    return result


def detect_multi_timeframe_pattern(
    snapshots: list[dict[str, Any]],
) -> Pattern | None:
    """Detect a pattern across multiple timeframe windows for one product.

    ``snapshots`` should contain the most recent classification for each
    timeframe (1d, 7d, 14d, 30d, 90d) for a single product, sorted from
    longest to shortest window (90d first, 1d last).

    Returns
    -------
    A ``Pattern`` enum value describing the cross-timeframe momentum, or
    ``None`` if no clear pattern is detected.
    """
    if len(snapshots) < 2:
        return None

    statuses = [s.get("status", "unknown") for s in snapshots]
    slopes = [float(s.get("slope_pct_per_day", 0.0) or 0.0) for s in snapshots]

    # Accelerating: every window is rising and slope increases toward the
    # shortest (most recent) window.
    if all(s == "rising" for s in statuses):
        if all(slopes[i] < slopes[i + 1] for i in range(len(slopes) - 1)):
            return Pattern.ACCELERATING
        return Pattern.SUSTAINED_GROWTH

    # Peaking: long-term windows were rising, but the shortest window is
    # now declining.
    if statuses[-1] == "declining" and all(s == "rising" for s in statuses[:-1]):
        return Pattern.PEAKING

    # Rebounding: long-term windows were declining, but the shortest window
    # is now rising.
    if statuses[-1] == "rising" and all(s == "declining" for s in statuses[:-1]):
        return Pattern.REBOUNDING

    # Cooling: shortest window has flattened after a longer rise.
    if statuses[-1] == "stable" and all(s == "rising" for s in statuses[:-1]):
        return Pattern.COOLING

    # Breakout: shortest window is rising while all longer windows were
    # stable or unknown.
    if statuses[-1] == "rising" and all(s in ("stable", "unknown") for s in statuses[:-1]):
        return Pattern.BREAKOUT

    # Declining across the board.
    if all(s == "declining" for s in statuses):
        return Pattern.DECLINING

    # Steady across the board.
    if all(s == "stable" for s in statuses):
        return Pattern.STEADY

    return None
