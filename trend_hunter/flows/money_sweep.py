"""Money sweep — daily cycle: ingest -> classify -> aggregate -> arbitrage -> scaffold top N.

Wraps the relevant pieces so a single `make money` runs everything end-to-end.
"""

from __future__ import annotations

from loguru import logger

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.adapters.supplier_catalog import CsvCatalogSupplier, MockSupplier
from trend_hunter.money.arbitrage import scan, summary
from trend_hunter.money.scaffold_pipeline import scaffold


def run(
    storage: DuckDBStorage,
    *,
    catalog_path: str = "./data/suppliers.csv",
    use_mock_if_missing: bool = True,
    top_scaffold: int = 3,
    dry_run: bool = True,
) -> dict:
    """Run arbitrage scan + scaffold the top-N profitable matches."""
    from pathlib import Path as _P

    path = _P(catalog_path)
    supplier = CsvCatalogSupplier(path)
    if not supplier._items and use_mock_if_missing:
        logger.warning(f"money sweep: no suppliers at {path}, falling back to MockSupplier")
        supplier = MockSupplier()  # type: ignore[assignment]

    rows = scan(storage, supplier)
    summary_dict = summary(rows)
    logger.info(f"money sweep: {summary_dict} — scaffolding top {top_scaffold}")
    scaffolds = scaffold(rows, top=top_scaffold, dry_run=dry_run)
    return {
        "summary": summary_dict,
        "scaffolded": scaffolds,
        "scaffold_count": len(scaffolds),
        "rows_in_supplier": len(supplier._items) if hasattr(supplier, "_items") else 0,
    }
