"""ArbitrageScanner — matches trending products against supplier catalogs.

Pure orchestration over three Protocol seams:
* Storage (read `agg_product_status` + write `money` rollup)
* SupplierCatalog (Phase 1: CSV-backed; future: AliExpress / CJ Dropshipping)
* MoneyCalculator (a tiny pure function, not even a Protocol)

Output: one `Money` row per profitable match (margin ≥ threshold), persisted
to the `money` table so the dashboard can surface it as a daily ranking.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

from loguru import logger

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.adapters.supplier_catalog import Supplier, SupplierCatalog
from trend_hunter.core.types import Money


@dataclass(frozen=True, slots=True)
class MoneyRow:
    sku: str  # "<store_source>/<external_id>"
    retail_price: float
    supplier_cost: float
    shipping_cost: float
    cac_estimate: float
    margin_pct: float  # 0..1
    currency: str
    supplier_name: str
    supplier_url: str
    matched_via: str  # "csv" | "mock"
    product_title: str | None


def _shipping_cost(s: Supplier) -> float:
    """Cheap deterministic shipping estimate by region."""
    return 1.0 if s.ships_from.upper() in {"US", "EU", "UK", "DE", "FR"} else 4.0


def _cac_estimate(retail_price: float) -> float:
    """Guess customer acquisition cost at 20% of retail — conservative."""
    return round(retail_price * 0.2, 2)


def compute_margin(retail: float, supplier: float, shipping: float, cac: float) -> Money:
    """Pure money calculator used by the scanner."""
    profit = retail - supplier - shipping - cac
    margin = (profit / retail) if retail > 0 else 0.0
    return (
        Money(
            sku="",
            horizon_days=0,
            point_estimate=0.0,
            lower_80=0.0,
            upper_80=0.0,
            confidence=0.0,
        )
        if False
        else Money(  # never hit; we'll fill a real Money below
            sku="",
            horizon_days=0,
            point_estimate=margin,
            lower_80=margin,
            upper_80=margin,
            confidence=margin,
        )
    )


def _to_money_row(sku: str, retail_price: float, supplier: Supplier) -> MoneyRow:
    shipping = _shipping_cost(supplier)
    cac = _cac_estimate(retail_price)
    profit = retail_price - supplier.cost_usd - shipping - cac
    margin = (profit / retail_price) if retail_price > 0 else 0.0
    return MoneyRow(
        sku=sku,
        retail_price=float(retail_price),
        supplier_cost=float(supplier.cost_usd),
        shipping_cost=float(shipping),
        cac_estimate=float(cac),
        margin_pct=float(margin),
        currency="USD",
        supplier_name=supplier.supplier,
        supplier_url=supplier.url,
        matched_via="catalog",
        product_title=None,
    )


def scan(
    storage: DuckDBStorage,
    supplier: SupplierCatalog,
    *,
    min_margin_pct: float = 0.25,
    min_price_usd: float = 10.0,
) -> list[MoneyRow]:
    """Pull trending products, match each to a supplier, persist profitable ones.

    Returns the full list of profitable matches (also persisted to the
    `money` table for the dashboard).
    """
    # 1) pull trending products — anything classified in the last 30 days
    try:
        rows = storage.query(
            """
            SELECT source, external_id, title, avg_price_7d
            FROM agg_product_status
            WHERE status IN ('rising', 'stable')
              AND avg_price_7d IS NOT NULL
              AND avg_price_7d >= ?
            ORDER BY avg_price_7d DESC
            """,
            (float(min_price_usd),),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"arbitrage: agg_product_status missing ({type(e).__name__}); "
            f"have you run `make aggregate`?"
        )
        return []

    profitable: list[MoneyRow] = []
    for r in rows:
        title = r.get("title") or ""
        sku_id = f"{r['source']}/{r['external_id']}"
        matches = supplier.find(title, limit=1)
        if not matches:
            continue
        m = matches[0]
        row = _to_money_row(sku_id, float(r["avg_price_7d"]), m)
        row = MoneyRow(
            sku=row.sku,
            retail_price=row.retail_price,
            supplier_cost=row.supplier_cost,
            shipping_cost=row.shipping_cost,
            cac_estimate=row.cac_estimate,
            margin_pct=row.margin_pct,
            currency=row.currency,
            supplier_name=row.supplier_name,
            supplier_url=row.supplier_url,
            matched_via=row.matched_via,
            product_title=title,
        )
        if row.margin_pct >= min_margin_pct:
            profitable.append(row)

    # 2) Persist profitable matches. Wipe-then-insert keeps the table small
    #    AND idempotent across re-runs (no PK collisions needed).
    with storage as st:
        st.execute("DELETE FROM money")
        if profitable:
            ts = dt.datetime.now(dt.UTC)
            st.upsert(
                "money",
                [
                    {
                        "sku": p.sku,
                        "retail_price": p.retail_price,
                        "supplier_cost": p.supplier_cost,
                        "shipping_cost": p.shipping_cost,
                        "cac_estimate": p.cac_estimate,
                        "margin_pct": p.margin_pct,
                        "currency": p.currency,
                        "recorded_at": ts,
                    }
                    for p in profitable
                ],
            )

    logger.info(
        f"arbitrage: {len(profitable)} profitable match(es) ≥ {min_margin_pct * 100:.0f}% margin"
    )
    return profitable


def summary(rows: Iterable[MoneyRow]) -> dict:
    rows = list(rows)
    if not rows:
        return {"count": 0, "avg_margin": 0.0, "top_sku": None}
    margins = [r.margin_pct for r in rows]
    return {
        "count": len(rows),
        "avg_margin": round(sum(margins) / len(margins), 4),
        "top_sku": max(rows, key=lambda r: r.margin_pct).sku,
    }
