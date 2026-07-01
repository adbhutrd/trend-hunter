"""Unit tests for the ArbitrageScanner math + supplier matching."""
from __future__ import annotations

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.adapters.supplier_catalog import CsvCatalogSupplier, MockSupplier
from trend_hunter.money.arbitrage import _cac_estimate, _shipping_cost, _to_money_row, scan, summary


def test_shipping_cost_by_region():
    from trend_hunter.adapters.supplier_catalog import Supplier
    u = Supplier("*x*", "u", "u", "u", 1.0, "US", 5)
    cn = Supplier("*x*", "c", "c", "c", 1.0, "CN", 12)
    assert _shipping_cost(u) == 1.0
    assert _shipping_cost(cn) == 4.0


def test_cac_estimate_is_20pct_of_retail():
    assert _cac_estimate(100.0) == 20.0
    assert _cac_estimate(50.0) == 10.0


def test_margin_math_high_margin_match():
    from trend_hunter.adapters.supplier_catalog import Supplier
    s = Supplier("*desk lamp*", "lum-001", "Lumify",
                  "https://lumify.example.com/p/001", 8.40, "CN", 12)
    row = _to_money_row("shopify/abc", retail_price=49.99, supplier=s)
    # profit = 49.99 - 8.40 - 4.00 - 9.998 ≈ 27.59
    # margin  = 27.59 / 49.99 ≈ 0.552
    assert row.margin_pct >= 0.45
    assert row.supplier_name == "Lumify"


def test_margin_math_zero_margin_match():
    from trend_hunter.adapters.supplier_catalog import Supplier
    s = Supplier("*desk lamp*", "x", "x", "x", 30.0, "US", 5)
    row = _to_money_row("shopify/abc", retail_price=33.0, supplier=s)
    # profit = 33 - 30 - 1 - 6.6 ≈ -4.6  ⇒ negative margin
    assert row.margin_pct < 0


def test_mock_supplier_matches():
    m = MockSupplier()
    matches = m.find("modern LED desk lamp", limit=3)
    assert len(matches) >= 1
    assert "lamp" in matches[0].title_pattern


def test_mock_supplier_no_match():
    m = MockSupplier()
    assert m.find("something boring and unrelated") == []


def test_csv_catalog_returns_empty_when_file_missing(tmp_path):
    cat = CsvCatalogSupplier(tmp_path / "no.csv")
    assert cat.find("anything") == []


def test_scan_with_no_data_returns_empty_list(storage: DuckDBStorage):
    s = MockSupplier()
    rows = scan(storage, s)
    # fresh DB has no products ⇒ no trending ⇒ no opportunities
    assert rows == []
    assert summary(rows) == {"count": 0, "avg_margin": 0.0, "top_sku": None}


def test_summary_with_rows():
    from trend_hunter.adapters.supplier_catalog import Supplier
    s = Supplier("*anything*", "x", "x", "x", 5.0, "CN", 12)
    rows = [
        _to_money_row(f"shopify/{i}", retail_price=50.0, supplier=s) for i in range(3)
    ]
    sum_ = summary(rows)
    assert sum_["count"] == 3
    assert sum_["top_sku"] in {r.sku for r in rows}
