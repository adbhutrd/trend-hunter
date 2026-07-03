"""Shopify Admin API push for one product.

Driven by environment:
  TH_SHOPIFY_DOMAIN   e.g. "my-store.myshopify.com"
  TH_SHOPIFY_TOKEN    e.g. "shpat_xxx..." (Admin API access token)

If either is missing we DO NOT push — we log a dry-run summary instead, so
operators can rehearse the scaffolding flow with zero blast radius.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger


@dataclass(frozen=True, slots=True)
class ProductDraft:
    sku: str
    title: str
    body_html: str
    price_usd: float
    sku_internal: str


class ShopifyPusher:
    name = "shopify-admin"

    def __init__(
        self,
        *,
        shop_domain: str | None = None,
        admin_token: str | None = None,
        api_version: str = "2024-01",
        timeout_s: float = 15.0,
    ) -> None:
        import os

        self.shop_domain = shop_domain or os.environ.get("TH_SHOPIFY_DOMAIN")
        self.admin_token = admin_token or os.environ.get("TH_SHOPIFY_TOKEN")
        self.api_version = api_version
        self.timeout_s = timeout_s

    @property
    def is_configured(self) -> bool:
        return bool(self.shop_domain and self.admin_token)

    async def push(self, draft: ProductDraft, *, dry_run: bool = True) -> dict[str, Any]:
        """Push one product. Returns dict with status, id (when live), error."""
        if dry_run or not self.is_configured:
            logger.info(
                f"[shopify dry-run] sku={draft.sku} title={draft.title!r} "
                f"price=${draft.price_usd:.2f}",
            )
            return {
                "status": "dry_run" if dry_run else "misconfigured",
                "sku": draft.sku,
                "title": draft.title,
                "price_usd": draft.price_usd,
            }

        url = f"https://{self.shop_domain}/admin/api/{self.api_version}/products.json"
        payload = {
            "product": {
                "title": draft.title,
                "body_html": draft.body_html,
                "vendor": "trend-hunter",
                "product_type": "scaffolded",
                "variants": [
                    {
                        "sku": draft.sku_internal,
                        "price": f"{draft.price_usd:.2f}",
                    },
                ],
            },
        }
        headers = {
            "X-Shopify-Access-Token": self.admin_token or "",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            r = await client.post(url, headers=headers, json=payload)

        if 200 <= r.status_code < 300:
            data = (
                r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            )
            product_id = data.get("product", {}).get("id")
            logger.success(f"[shopify live] pushed sku={draft.sku} id={product_id}")
            return {"status": "pushed", "id": product_id, "sku": draft.sku}

        logger.error(f"[shopify live] sku={draft.sku} failed: {r.status_code} {r.text[:200]}")
        return {
            "status": "failed",
            "sku": draft.sku,
            "error": r.text,
            "status_code": r.status_code,
        }


# ── sync bridge used by the CLI (asyncio.run one-shot) ────────────────────────
def push_sync(draft: ProductDraft, *, dry_run: bool = True, **kwargs) -> dict[str, Any]:
    """Convenience wrapper for CLI callers."""
    return asyncio.run(ShopifyPusher(**kwargs).push(draft, dry_run=dry_run))
