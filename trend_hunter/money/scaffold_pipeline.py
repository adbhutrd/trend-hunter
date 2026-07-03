"""Scaffolder pipeline — profitable MoneyRow → Shopify ProductDraft → push.

Operator flow:
    python -m trend_hunter.cli scaffold --top 3
        ⇒ drafts 3 ProductDrafts from the highest-margin money rows
        ⇒ prints what would push to the store (default dry-run)
        ⇒ if TH_SHOPIFY_DOMAIN + TH_SHOPIFY_TOKEN are set, real Admin API push
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from loguru import logger

from trend_hunter.adapters.shopify_push import ProductDraft, ShopifyPusher
from trend_hunter.money.arbitrage import MoneyRow


def draft_from_row(row: MoneyRow) -> ProductDraft:
    """Build a Shopify product draft from a profitable MoneyRow."""
    return ProductDraft(
        sku=row.sku,
        title=row.product_title or row.sku,
        body_html=(
            f"<p>Sourced via <a href='{row.supplier_url}'>{row.supplier_name}</a>. "
            f"Retail: ${row.retail_price:.2f}. "
            f"Cost: ${row.supplier_cost:.2f}. "
            f"Shipping: ${row.shipping_cost:.2f}. "
            f"Projected margin: {row.margin_pct * 100:.0f}%.</p>"
        ),
        price_usd=row.retail_price,
        sku_internal=row.sku.replace("/", "-"),
    )


def scaffold(
    rows: Iterable[MoneyRow],
    *,
    top: int = 3,
    dry_run: bool = True,
) -> list[dict]:
    """Convert the top-N money rows into Shopify drafts and push (or dry-run).

    Sync wrapper around the async ShopifyPusher — uses asyncio.run internally
    so the CLI can drive a one-shot scaffold from a non-async caller. The
    pusher itself remains async so swapping to aPlaywright-like transport
    later doesn't require a rewrite of the call site.
    """
    rows = list(rows)
    if not rows:
        logger.info("scaffold: no profitable rows; nothing to do.")
        return []
    targets = rows[: max(0, int(top))]
    pusher = ShopifyPusher()

    async def _run_all() -> list[dict]:
        return [await pusher.push(draft_from_row(r), dry_run=dry_run) for r in targets]

    return asyncio.run(_run_all())
