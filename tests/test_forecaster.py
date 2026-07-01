"""Unit tests for BaselineForecaster."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trend_hunter.adapters.forecaster_baseline import BaselineForecaster
from trend_hunter.core.types import Health  # noqa: F401  (sanity import)


def _ts(days_ago: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days_ago)


def test_forecast_returns_zero_when_too_few_points():
    fc = BaselineForecaster(min_points=3)
    out = fc.fit_predict([(_ts(1), 10.0)])
    assert out.point_estimate == 0.0
    assert out.confidence == 0.0


def test_forecast_basic_ewma():
    fc = BaselineForecaster(alpha=0.5, min_points=3)
    history = [(_ts(6 - i), 10.0 + 1.5 * i) for i in range(7)]
    out = fc.fit_predict(history)
    assert out.confidence > 0
    assert out.lower_80 <= out.point_estimate <= out.upper_80


def test_forecast_confidence_saturates_with_sample_size():
    fc = BaselineForecaster(min_points=3)
    big = [(_ts(i), 10.0 + 0.1 * i) for i in range(50)]
    out = fc.fit_predict(big, horizon_days=14)
    assert out.confidence == 1.0


def test_forecast_ci_widens_with_volatility():
    fc = BaselineForecaster(min_points=3)
    flat = [(_ts(i), 10.0 + 0.001 * i) for i in range(20)]
    volatile = [(_ts(i), 10.0 + 5.0 * ((-1) ** i)) for i in range(20)]
    out_flat = fc.fit_predict(flat)
    out_volatile = fc.fit_predict(volatile)
    flat_width = out_flat.upper_80 - out_flat.lower_80
    vol_width = out_volatile.upper_80 - out_volatile.lower_80
    assert vol_width > flat_width
