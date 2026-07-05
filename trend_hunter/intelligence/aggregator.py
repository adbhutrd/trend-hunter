"""Aggregator — nightly roll-up of raw rows into pre-aggregated tables.

We deliberately keep aggregates *inside* DuckDB (not separate Parquet on
disk) for Phase 1; the dashboard reads those tables with sub-second
DuckDB query latency and no extra filesystem dance. Large history is
parquet-exported in Phase 2 via DuckDB's native Parquet writer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from loguru import logger

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.intelligence.classifier import classify_one
from trend_hunter.observe.calibrate import auto_resolve_calibrations


# ── aggregates ───────────────────────────────────────────────────────────────
def aggregate(storage: DuckDBStorage) -> dict:
    """Build the dashboard tables from raw rows.

    Idempotent — running twice produces the same state for the same source
    data.  Side-effect: on every call, after building roll-up tables, also
    auto-resolves any forecast calibrations that have passed their horizon
    (see :func:`trend_hunter.observe.calibrate.auto_resolve_calibrations`).
    The number resolved is returned as ``calibrations_resolved`` so callers
    can surface it in the ``run_history`` counter JSON.

    Returns
    -------
    dict
        ``{"ran_at": str, "products": int, "sources": int,
        "calibrations_resolved": int}``
    """
    c = storage.conn()
    now = datetime.now(UTC)

    # Materialise raw → classified enrichment for every product with ≥3 obs.
    c.execute(
        """
        CREATE OR REPLACE TABLE agg_product_status AS
        WITH recent AS (
            SELECT
                source,
                external_id,
                any_value(title)        AS title,
                count(*)                AS n,
                min(captured_at)        AS first_seen,
                max(captured_at)        AS last_seen,
                avg(price)              AS avg_price_7d
            FROM products
            WHERE captured_at >= now() - INTERVAL 30 DAY
            GROUP BY source, external_id
            HAVING count(*) >= 3
        )
        SELECT
            source,
            external_id,
            title,
            n,
            first_seen,
            last_seen,
            avg_price_7d
        FROM recent
        ORDER BY n DESC, last_seen DESC
        """,
    )

    # Per-source last-seen summary for Health page
    c.execute(
        """
        CREATE OR REPLACE TABLE agg_source_health AS
        SELECT
            source,
            state,
            last_run,
            last_ok,
            rows_in,
            error_rate,
            detail
        FROM health
        ORDER BY source
        """,
    )

    # Niche saturation top-N for the dashboard
    c.execute(
        """
        CREATE OR REPLACE TABLE agg_saturation AS
        SELECT
            source,
            count(DISTINCT external_id) AS products,
            count(*)                     AS observations
        FROM products
        WHERE captured_at >= now() - INTERVAL 7 DAY
        GROUP BY source
        ORDER BY products DESC
        """,
    )

    logger.info(
        f"aggregator: built 3 roll-up tables at {now.isoformat()}; "
        f"now auto-resolving overdue calibrations",
    )
    resolved = auto_resolve_calibrations(storage)
    logger.info(
        f"auto-resolved {resolved['resolved']} calibration(s) "
        f"({resolved['skipped']} skipped)",
    )
    return {
        "ran_at": now.isoformat(),
        "products": c.execute("SELECT count(*) FROM agg_product_status").fetchone()[0],
        "sources": c.execute("SELECT count(*) FROM agg_source_health").fetchone()[0],
        "calibrations_resolved": resolved["resolved"],
    }


def classify_all(storage: DuckDBStorage) -> int:
    """Run classify_one() over every product with ≥3 price observations.

    Writes per-(source, external_id) classifications into
    `agg_product_status` (replaces the table created by aggregate()).
    Cheap, idempotent. Used by dashboard auto-refresh.
    """
    c = storage.conn()
    rows = c.execute(
        """
        SELECT source, external_id, title, captured_at, price
        FROM products
        WHERE captured_at >= now() - INTERVAL 30 DAY
          AND price IS NOT NULL
        ORDER BY source, external_id, captured_at
        """,
    ).fetchall()

    by_key: dict[
        tuple[str, str], tuple[str | None, list[tuple[str, str, datetime, float | None]]]
    ] = {}
    for src, ext, title, ts, price in rows:
        key = (src, ext)
        if key not in by_key:
            by_key[key] = (title, [])
        by_key[key][1].append((src, ext, ts, price))

    classified = []
    for (src, ext), (title, hist) in by_key.items():
        c_p = classify_one(hist)
        if c_p is None:
            continue
        classified.append(
            {
                "source": src,
                "external_id": ext,
                "title": title,
                "status": c_p.status,
                "slope_pct_per_day": c_p.slope_pct_per_day,
                "n_points": c_p.n_points,
                "window_start": c_p.window_start,
                "window_end": c_p.window_end,
            }
        )

    if classified:
        pd.DataFrame(classified)
        c.execute("DROP TABLE IF EXISTS agg_product_status")
        c.execute("CREATE TABLE agg_product_status AS SELECT * FROM df")
    else:
        # Empty DB / no rows yet — keep the schema so dashboard readers don't crash.
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS agg_product_status (
                source             TEXT,
                external_id        TEXT,
                title              TEXT,
                status             TEXT,
                slope_pct_per_day  DOUBLE,
                n_points           INTEGER,
                window_start       TIMESTAMP,
                window_end         TIMESTAMP
            )
            """,
        )

    logger.info(f"classifier: wrote classification for {len(classified)} products")
    return len(classified)
