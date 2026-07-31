"""ML Forecaster — linear-regression trend detection on raw price history.

Predicts whether a product's price will rise, decline, or stay stable
over the next 7, 14, and 30 days.

**Why linear regression instead of Prophet?**
E-commerce product data has:
  - Short histories (5-30 observations)
  - Infrequent price changes
  - No discernible seasonality at daily/weekly level
  - High variance relative to price level

Prophet over-extrapolates on short noisy series and requires >10x more
data to be useful.  A simple OLS trend line is:
  - Stable with as few as 3 observations
  - Computes in microseconds (vs seconds for Prophet)
  - Gives interpretable R²-based confidence
  - No external dependency (no Prophet install needed)

Thresholds (configurable via module constants):
  - RISING: predicted change > +3% over the forecast horizon
  - DECLINING: predicted change < -3%
  - BREAKOUT: predicted change > +10% (strong buy signal)

Confidence is based on:
  1. R-squared (fit quality) — how well the trend line explains the data
  2. Price volatility (CV = std/mean) — less volatile = more confidence
  3. Formula: conf = max(0.10, sqrt(R²) × 3) × max(0, 1 - CV)
     - sqrt(R²) spreads low values (R²=0.01 → sqrt=0.10 for 30% conf)
     - ×3 scales signal up so trends are visible
     - Minimum 10% floor ensures even weak signals are shown
     - vol_penalty = max(0, 1 - CV) penalizes volatile products
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from loguru import logger

from trend_hunter.adapters.storage_duckdb import DuckDBStorage

# ── config ────────────────────────────────────────────────────────────────────
MIN_HISTORY_POINTS = 3  # minimum data points to compute a trend
FORECAST_HORIZONS = [7, 14, 30]  # days to forecast
RISING_THRESHOLD = 0.03  # 3% price increase over horizon
BREAKOUT_THRESHOLD = 0.10  # 10% price increase — strong signal
DECLINING_THRESHOLD = -0.03  # 3% price decrease over horizon


def _ensure_ml_predictions_table(storage: DuckDBStorage) -> None:
    """Create the ml_predictions table if it doesn't exist."""
    storage.execute("""
        CREATE TABLE IF NOT EXISTS ml_predictions (
            source VARCHAR,
            external_id VARCHAR,
            title VARCHAR,
            horizon_days INTEGER,
            predicted_slope DOUBLE,
            predicted_status VARCHAR,
            confidence DOUBLE,
            lower_bound DOUBLE,
            upper_bound DOUBLE,
            forecasted_at TIMESTAMP
        )
    """)


def run_ml_forecast(storage: DuckDBStorage) -> dict[str, Any]:
    """Run linear-regression trend forecast on all eligible products.

    Returns summary with counts per predicted status.
    """
    _ensure_ml_predictions_table(storage)

    # Fetch raw price history from products table
    rows = storage.query("""
        SELECT source, external_id, title, captured_at, price
        FROM products
        WHERE price IS NOT NULL AND price > 0
          AND source = 'shopify'
        ORDER BY source, external_id, captured_at ASC
    """)

    if not rows:
        logger.info("ml_forecaster: no product data available")
        return {"products": 0, "predictions": 0}

    # Group by product
    products: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        products[(r["source"], r["external_id"])].append(r)

    total_predictions = 0
    status_counts: dict[str, int] = defaultdict(int)
    predictions_batch: list[dict] = []
    now = datetime.now(UTC)

    for (source, ext_id), hist in products.items():
        if len(hist) < MIN_HISTORY_POINTS:
            continue

        title = hist[-1].get("title", ext_id) or ext_id
        prices = [
            (r["captured_at"].timestamp(), float(r["price"]))
            for r in hist
            if r["price"] is not None and r["price"] > 0
        ]
        if len(prices) < MIN_HISTORY_POINTS:
            continue

        try:
            preds = _forecast_product(source, ext_id, title, prices, now)
            for p in preds:
                predictions_batch.append(p)
                status_counts[p["predicted_status"]] += 1
                total_predictions += 1
        except Exception as e:
            logger.debug(f"ml_forecaster: {source}/{ext_id} failed: {e}")
            continue

    # Write predictions to DB: clear old, insert fresh
    if predictions_batch:
        storage.execute("DELETE FROM ml_predictions")
        _batch_write(storage, "ml_predictions", predictions_batch)

    logger.info(
        f"ml_forecaster: {len(predictions_batch)} predictions for {len(products)} products: "
        f"{dict(status_counts)}"
    )

    return {
        "products": len(products),
        "predictions": total_predictions,
        "status_counts": dict(status_counts),
    }


def _batch_write(storage: DuckDBStorage, table: str, rows: list[dict]) -> None:
    """Insert rows via DuckDB's pandas bridge."""
    if not rows:
        return
    c = storage.conn()
    df = pd.DataFrame(rows)
    cols = storage._columns(table)
    df = df.reindex(columns=cols)
    c.execute(f"INSERT INTO {table} SELECT * FROM df")


def _ols_slope(
    xs: list[float], ys: list[float]
) -> tuple[float, float, float]:
    """Ordinary least squares slope, intercept, and R².

    Returns (slope, intercept, r_squared).
    slope is in price-per-second units (since xs are Unix timestamps).
    """
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0, 0.0

    mx = sum(xs) / n
    my = sum(ys) / n

    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0.0:
        return 0.0, my, 0.0

    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    slope = sxy / sxx
    intercept = my - slope * mx

    # R-squared
    ss_res = sum((ys[i] - (slope * xs[i] + intercept)) ** 2 for i in range(n))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return slope, intercept, r_squared


def _forecast_product(
    source: str,
    ext_id: str,
    title: str,
    prices: list[tuple[float, float]],  # (timestamp, price)
    now: datetime,
) -> list[dict]:
    """Compute linear trend forecast for one product across all horizons.

    prices is sorted [(timestamp, price), ...] with timestamp in seconds.
    """
    xs = [p[0] for p in prices]
    ys = [p[1] for p in prices]

    slope, intercept, r_squared = _ols_slope(xs, ys)

    if slope == 0.0:
        # Flat line — predict stable for all horizons
        return [
            {
                "source": source,
                "external_id": ext_id,
                "title": title[:200],
                "horizon_days": h,
                "predicted_slope": 0.0,
                "predicted_status": "stable",
                "confidence": 0.95 if r_squared > 0.5 else 0.5,
                "lower_bound": 0.0,
                "upper_bound": 0.0,
                "forecasted_at": now,
            }
            for h in FORECAST_HORIZONS
        ]

    # Calculate predicted % change over each horizon
    last_price = ys[-1]
    seconds_per_day = 86400.0

    # `slope` is price/second.  price/day = slope * 86400.
    # % change over horizon = (slope * 86400 * horizon_days) / last_price
    price_per_day = slope * seconds_per_day

    predictions = []
    for horizon in FORECAST_HORIZONS:
        predicted_change = (price_per_day * horizon) / last_price

        # Prediction interval: wider for longer horizons, more volatile prices
        # Std dev of residuals gives us a measure of prediction uncertainty
        residuals = [ys[i] - (slope * xs[i] + intercept) for i in range(len(xs))]
        resid_std = statistics.stdev(residuals) if len(residuals) >= 2 else 0.0

        # Prediction interval grows with horizon and residual std
        # 80% CI ≈ ±1.28 × residual_std × sqrt(horizon_days / days_of_data)
        days_cover = max(1.0, (xs[-1] - xs[0]) / seconds_per_day)
        ci_half = 1.28 * resid_std * (horizon / max(1.0, days_cover)) ** 0.5

        predicted_price = last_price + price_per_day * horizon
        lower_price = predicted_price - ci_half
        upper_price = predicted_price + ci_half

        lower_change = (lower_price - last_price) / last_price
        upper_change = (upper_price - last_price) / last_price

        # Classify
        if predicted_change >= BREAKOUT_THRESHOLD:
            status = "breakout"
        elif predicted_change >= RISING_THRESHOLD:
            status = "rising"
        elif predicted_change <= DECLINING_THRESHOLD:
            status = "declining"
        else:
            status = "stable"

        # Confidence: sqrt(R²) × scaling × volatility penalty + 10% floor
        # sqrt(R²) spreads low R² values (0.01 → 0.10, 0.04 → 0.20)
        # ×3 scales signal up so products with any trend show readable confidence
        cv = resid_std / last_price if last_price > 0 else 1.0
        vol_penalty = max(0.0, 1.0 - cv)
        r_score = (r_squared ** 0.5) * 3.0  # sqrt + scale
        confidence = round(max(0.10, r_score) * vol_penalty, 4)

        predictions.append(
            {
                "source": source,
                "external_id": ext_id,
                "title": title[:200],
                "horizon_days": horizon,
                "predicted_slope": round(predicted_change, 4),
                "predicted_status": status,
                "confidence": min(1.0, max(0.0, confidence)),
                "lower_bound": round(lower_change, 4),
                "upper_bound": round(upper_change, 4),
                "forecasted_at": now,
            }
        )

    return predictions
