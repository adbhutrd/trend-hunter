"""WooCommerce REST API scraper — fetches products from public WooCommerce stores.

WooCommerce (the most popular e-commerce platform) provides a public REST API
that many stores leave open for reading product data. No API key needed for
read-only product access on many public stores.

This adapter fetches /wp-json/wc/v3/products from configured WooCommerce stores.
Each product becomes a RawSignal with price, URL, and metadata.

Implements core.ports.Scraper so it slots straight into the ingest runner.
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx
from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class WooCommerceScraper:
    name = "woocommerce"

    def __init__(
        self,
        stores: list[str],
        *,
        max_per_store: int = 50,
        timeout_s: float = 20.0,
    ) -> None:
        self.stores = [s.rstrip("/") for s in stores]
        self.max_per_store = max_per_store
        self.timeout_s = timeout_s

    async def fetch(self) -> list[RawSignal]:
        signals: list[RawSignal] = []
        now = datetime.now(UTC)
        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        ) as client:
            for store in self.stores:
                try:
                    batch = await self._fetch_store(client, store, now)
                    signals.extend(batch)
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"woocommerce: {store} failed: {type(e).__name__}: {e}")
        logger.info(f"woocommerce: ingested {len(signals)} products across {len(self.stores)} stores")
        return signals

    async def health(self) -> Health:
        ok = bool(self.stores)
        return Health(
            source=self.name,
            state=HealthState.OK if ok else HealthState.UNKNOWN,
            last_run=None,
            last_ok=None,
            rows_in=0,
            error_rate=0.0,
            detail=f"{len(self.stores)} store(s) configured",
        )

    async def _fetch_store(
        self,
        client: httpx.AsyncClient,
        store_url: str,
        run_ts: datetime,
    ) -> list[RawSignal]:
        """Fetch products from one WooCommerce store via its public REST API."""
        api_url = f"{store_url}/wp-json/wc/v3/products"
        params = {
            "per_page": str(min(self.max_per_store, 100)),
            "orderby": "popularity",
            "order": "desc",
        }

        try:
            r = await client.get(api_url, params=params)
            if r.status_code == 404:
                # Try alternative path
                api_url = f"{store_url}/wp-json/wc/store/v1/products"
                r = await client.get(api_url, params={"per_page": str(min(self.max_per_store, 100))})
            if r.status_code == 403:
                logger.info(f"woocommerce: {store_url} API restricted (403), using public RSS/HTML")
                return await self._fetch_store_fallback(client, store_url, run_ts)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.debug(f"woocommerce: {store_url} -> {e.response.status_code} (not a WooCommerce store?)")
            return []
        except Exception as e:
            logger.debug(f"woocommerce: {store_url} -> {type(e).__name__}: {e}")
            return []

        try:
            products = r.json()
        except Exception as e:
            logger.warning(f"woocommerce: {store_url} JSON parse failed: {e}")
            return []

        if isinstance(products, dict):
            products = products.get("data", products.get("products", [products]))

        signals = []
        for p in (products or []):
            if isinstance(p, dict):
                pid = p.get("id") or p.get("slug") or str(hash(str(p)))
                title = p.get("name") or p.get("title") or "Unknown"
                price = _extract_price(
                    p.get("price") or
                    p.get("regular_price") or
                    p.get("sale_price") or
                    ""
                )
                permalink = p.get("permalink") or p.get("link") or p.get("url") or store_url
                # Handle nested images/thumbnails
                img_url = ""
                images = p.get("images", [])
                if images and isinstance(images, list) and len(images) > 0:
                    if isinstance(images[0], dict):
                        img_url = images[0].get("src") or images[0].get("url", "")

                signals.append(
                    RawSignal(
                        source=self.name,
                        external_id=str(pid),
                        captured_at=run_ts,
                        payload={
                            "title": str(title)[:200],
                            "price": price,
                            "currency": p.get("currency", "USD"),
                            "url": permalink,
                            "description": (p.get("description") or p.get("short_description") or "")[:500],
                            "image_url": img_url,
                            "sku": p.get("sku") or "",
                            "categories": ", ".join(
                                c.get("name", "") for c in (p.get("categories") or [])
                                if isinstance(c, dict)
                            ),
                        },
                    )
                )
        return signals

    async def _fetch_store_fallback(
        self,
        client: httpx.AsyncClient,
        store_url: str,
        run_ts: datetime,
    ) -> list[RawSignal]:
        """Fallback: try the store's RSS feed or sitemap for products."""
        signals = []
        # Try products RSS feed
        for feed_path in ["/feed/?post_type=product", "/products/feed/", "/shop/feed/"]:
            try:
                r = await client.get(f"{store_url}{feed_path}")
                if r.status_code == 200:
                    try:
                        root = ET.fromstring(r.content)
                        for item in root.findall(".//item"):
                            title_el = item.find("title")
                            link_el = item.find("link")
                            desc_el = item.find("description")
                            title = title_el.text if title_el is not None else "Unknown"
                            link = link_el.text if link_el is not None else store_url
                            price_match = re.search(r"\$([\d,]+\\.?\d*)", (desc_el.text or "") if desc_el is not None else "")
                            price = float(price_match.group(1).replace(",", "")) if price_match else None
                            signals.append(
                                RawSignal(
                                    source=self.name,
                                    external_id=f"wc-rss-{hash(title) & 0xFFFFFF}",
                                    captured_at=run_ts,
                                    payload={
                                        "title": title,
                                        "price": price,
                                        "currency": "USD",
                                        "url": link,
                                        "description": (desc_el.text or "")[:500] if desc_el is not None else "",
                                    },
                                )
                            )
                        if signals:
                            break
                    except Exception:
                        continue
            except Exception:
                continue
        return signals


def _extract_price(value) -> float | None:
    """Extract a float price from various formats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"([\d,]+\.?\d*)", str(value))
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None
