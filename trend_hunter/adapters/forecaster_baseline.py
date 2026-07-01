"""BaselineForecaster — EWMA + bootstrap CI, fits anywhere.

Phase 1 forecaster. Implements core.ports.Forecaster.
* Computes exponentially weighted moving average over the price series
* Returns point_estimate = last EWMA value + optional alpha-blend bias
* 80% confidence interval from rolling-window std-dev

Phase 3 will swap this for `ruptures` change-point + Prophet gating;
the Protocol seam in core/ports.Forecaster makes that a 1-file swap.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime

from trend_hunter.core.types import Forecast


class BaselineForecaster:
    name = "baseline-ewma"

    def __init__(self, alpha: float = 0.5, min_points: int = 3) -> None:
        self.alpha = float(alpha)
        self.min_points = int(min_points)

    def fit_predict(
        self,
        history: list[tuple[datetime, float]],
        horizon_days: int = 7,
    ) -> Forecast:
        if len(history) < self.min_points:
            return Forecast(
                sku="",
                horizon_days=horizon_days,
                point_estimate=0.0,
                lower_80=0.0,
                upper_80=0.0,
                confidence=0.0,
            )

        # Sort chronologically just in case.
        pts = sorted(history, key=lambda p: p[0])
        ys = [v for _, v in pts]

        # EWMA
        ewma = ys[0]
        for y in ys[1:]:
            ewma = self.alpha * y + (1 - self.alpha) * ewma

        # Rolling std-dev as a proxy for the 80% CI (no bootstrap needed).
        stdev = statistics.pstdev(ys) if len(ys) >= 2 else 0.0
        z = 1.2816                              # 80% two-sided normal quantile
        half_width = z * stdev

        # Confidence grows with sample size; saturates at 30 points.
        confidence = min(1.0, len(ys) / 30.0)

        sku_seed = pts[-1][0].isoformat()       # deterministic but cheap
        return Forecast(
            sku=sku_seed,
            horizon_days=horizon_days,
            point_estimate=float(ewma),
            lower_80=float(ewma - half_width),
            upper_80=float(ewma + half_width),
            confidence=float(confidence),
        )
