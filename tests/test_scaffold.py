"""Unit tests for the scaffold pipeline (draft builder + push dry-run logic)."""
from __future__ import annotations

import pytest

from trend_hunter.adapters.shopify_push import ShopifyPusher, ProductDraft
from trend_hunter.money.scaffold_pipeline import draft_from_row, scaffold
from trend_hunter.money.arbitrage import MoneyRow


def _sample_row(margin: float = 0.4) -> MoneyRow:
    return MoneyRow(
        sku="shopify/abc-123",
        retail_price=49.99,
        supplier_cost=8.40,
        shipping_cost=4.00,
        cac_estimate=9.998,
        margin_pct=margin,
        currency="USD",
        supplier_name="Lumify",
        supplier_url="https://lumify.example.com/p/001",
        matched_via="catalog",
        product_title="Modern LED Desk Lamp",
    )


@pytest.mark.asyncio
async def test_dry_run_does_not_through_when_unconfigured():
    pusher = ShopifyPusher()                          # no env vars
    assert pusher.is_configured is False
    drafted = ProductDraft(
        sku="x", title="t", body_html="b",
        price_usd=10.0, sku_internal="x",
    )
    out = await pusher.push(drafted, dry_run=True)
    assert out["status"] == "dry_run"


@pytest.mark.asyncio
async def test_push_returns_misconfigured_when_forced_live_without_env():
    pusher = ShopifyPusher()
    out = await pusher.push(
        ProductDraft(sku="x", title="t", body_html="b", price_usd=10.0, sku_internal="x"),
        dry_run=False,
    )
    assert out["status"] == "misconfigured"


def test_draft_from_row():
    drafted = draft_from_row(_sample_row())
    assert drafted.sku == "shopify/abc-123"
    assert "Lumify" in drafted.body_html
    assert drafted.price_usd == 49.99


def test_scaffold_top_n_dry_run():
    rows = [
        _sample_row(margin=0.5),
        _sample_row(margin=0.4),
        _sample_row(margin=0.3),
    ]
    out = scaffold(rows, top=2, dry_run=True)
    assert len(out) == 2
    assert all(r["status"] == "dry_run" for r in out)


def test_scaffold_empty_rows_returns_empty():
    assert scaffold([], top=3, dry_run=True) == []
