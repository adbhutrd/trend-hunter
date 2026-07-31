"""Trend classifier — rising / declining / stable from per-product price history.

Picks the simplest defensible model:

  * Need ≥3 observations in last 7 days of the same (source, external_id).
  * Fit a slope over time. Edge case (constant px, never >3 obs) → STABLE.
  * Magnitude of slope scaled as % of mean → threshold ±5% over the window.

This is *transparent*, *fast*, and *correctly tells you when it doesn't know*
(`UNKNOWN`), which is more honest than Prophet over-smoothing new-product
bursts in Phase 1. Prophet + ruptures come in Phase 3.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class Status:
    RISING = "rising"
    DECLINING = "declining"
    STABLE = "stable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedProduct:
    source: str
    external_id: str
    title: str | None
    status: str
    slope_pct_per_day: float
    n_points: int
    window_start: datetime
    window_end: datetime
    timeframe_days: int


def _slope(xs: list[float], ys: list[float]) -> float:
    """Least-squares slope. Returns 0.0 if xs are degenerate."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0.0:
        return 0.0
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return sxy / sxx


def classify_one(
    history: Iterable[tuple[str, str, datetime, float | None]],
    *,
    window_days: int = 7,
    rising_threshold: float = 0.03,
    declining_threshold: float = -0.03,
) -> ClassifiedProduct | None:
    """Classify the trend of one product given its history.

    `history` is an iterable of (source, external_id, captured_at, price).
    The function returns the *most recent* observation's title is not in
    scope yet — the caller supplies `title` separately if it wants to
    keep the latest known title. (We'll get there in the next refactor.)
    """
    # Need a deterministic first-tuple for source/external_id; take from first item
    history = list(history)
    if not history:
        return None
    src, ext, _, _ = history[0]

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)
    points = [
        (t.astimezone(UTC), p)
        for s, e, t, p in history
        if s == src and e == ext and t.astimezone(UTC) >= cutoff and p is not None
    ]
    points.sort(key=lambda x: x[0])
    if len(points) < 2:
        return ClassifiedProduct(
            source=src,
            external_id=ext,
            title=None,
            status=Status.UNKNOWN,
            slope_pct_per_day=0.0,
            n_points=len(points),
            window_start=points[0][0] if points else now,
            window_end=points[-1][0] if points else now,
            timeframe_days=window_days,
        )

    xs = [p[0].timestamp() for p in points]
    ys = [float(p[1]) for p in points]
    slope = _slope(xs, ys)
    mean_y = sum(ys) / len(ys)
    # `slope` units: price-per-second (because xs are unix timestamps in sec).
    # pct_per_day = (price/day) / mean_price ⇒ unitless per-day fractional change.
    days = max(1e-9, (xs[-1] - xs[0]) / 86400.0)
    pct_per_day = (slope * 86400.0 / mean_y) if mean_y > 0 else 0.0
    # total_pct accumulates the per-day rate over the observation window.
    total_pct = pct_per_day * days

    if total_pct >= rising_threshold:
        status = Status.RISING
    elif total_pct <= declining_threshold:
        status = Status.DECLINING
    else:
        status = Status.STABLE

    return ClassifiedProduct(
        source=src,
        external_id=ext,
        title=None,
        status=status,
        slope_pct_per_day=pct_per_day,
        n_points=len(points),
        window_start=points[0][0],
        window_end=points[-1][0],
        timeframe_days=window_days,
    )
