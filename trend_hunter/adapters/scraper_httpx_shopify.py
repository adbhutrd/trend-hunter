"""Shopify scraper — fetches ALL products from /products.json with pagination.

No auth, no key. Crawls every page of every store to collect every product.
Handles rate limits with per-store delays, browser-like headers, and jitter.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import httpx
from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

# Rotating User-Agent pool — mimics real browsers to avoid 429 blocks.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]


class ShopifyScraper:
    name = "shopify"

    def __init__(
        self,
        stores: list[str],
        *,
        max_concurrent: int = 3,
        per_store_delay_s: float = 0.5,  # delay between pages (lower=300s faster)
        timeout_s: float = 15.0,
        max_pages: int = 5,  # 5 pages × 250 products = 1250 max per store
    ) -> None:
        self.stores = stores
        self.max_concurrent = max(1, max_concurrent)
        self.per_store_delay_s = per_store_delay_s  # Base delay between pages
        self.timeout_s = timeout_s
        self.max_pages = max_pages
        self._store_sem = asyncio.Semaphore(self.max_concurrent)

    async def fetch(self) -> list[RawSignal]:
        signals: list[RawSignal] = []
        now = datetime.now(UTC)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
            follow_redirects=True,
        ) as client:
            tasks = [self._fetch_store(client, s, now) for s in self.stores]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"shopify: {self.stores[i]} failed: {result}")
                else:
                    signals.extend(result)
        logger.info(f"shopify: {len(signals)} products across {len(self.stores)} stores")
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
        """Fetch ALL products from one Shopify store using pagination.

        Uses per-store semaphore to limit concurrent store scraping,
        plus per-page jitter delay to avoid triggering Shopify rate limits.
        """
        async with self._store_sem:
            store_url = store_url.rstrip("/")
            signals = []
            page = 1
            consecutive_429s = 0
            max_429s = 3  # Give up after this many consecutive 429s on the same store

            while page <= self.max_pages and consecutive_429s < max_429s:
                # Random per-page delay (base + jitter)
                delay = self.per_store_delay_s + random.uniform(0.5, 2.0)
                await asyncio.sleep(delay)

                # Use a random User-Agent for each request
                ua = random.choice(_USER_AGENTS)
                target = f"{store_url}/products.json?page={page}&limit=250"
                headers = {
                    "User-Agent": ua,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": store_url,
                    "Cache-Control": "no-cache",
                }

                try:
                    r = await client.get(target, headers=headers)
                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    logger.debug(f"shopify: {target} connection error: {e}")
                    # Back off and retry page
                    await asyncio.sleep(5.0)
                    continue

                if r.status_code == 429:
                    consecutive_429s += 1
                    backoff = 5.0 * consecutive_429s + random.uniform(1.0, 3.0)
                    logger.debug(
                        f"shopify: 429 on {store_url} page {page} "
                        f"(attempt {consecutive_429s}/{max_429s}) — "
                        f"backoff {backoff:.0f}s"
                    )
                    await asyncio.sleep(backoff)
                    continue  # Retry same page

                consecutive_429s = 0  # Reset on success

                if r.status_code in (404, 410, 451):
                    break  # No more pages / store removed

                if r.status_code != 200:
                    logger.warning(
                        f"shopify: {target} -> {r.status_code} (page {page})"
                    )
                    break

                try:
                    data = r.json()
                except json.JSONDecodeError:
                    logger.debug(f"shopify: {target} bad JSON (page {page})")
                    break

                products = data.get("products") or []
                if not products:
                    break  # No more products

                for p in products:
                    pid = p.get("id")
                    if pid is None:
                        continue
                    p["_store_url"] = store_url
                    signals.append(
                        RawSignal(
                            source=self.name,
                            external_id=str(pid),
                            captured_at=run_ts,
                            payload=p,
                        )
                    )

                page += 1

            if page > 1:
                logger.info(
                    f"shopify: {store_url} -> {len(signals)} products "
                    f"({page - 1} pages, {consecutive_429s} rate-limits)"
                )
            return signals


def from_sources_json(path: Path = Path("./sources.json")) -> ShopifyScraper:
    """Build a ShopifyScraper from sources.json. Empty list if no stores."""
    if not path.exists():
        logger.warning(f"shopify: {path} not found, scraper will be empty")
        return ShopifyScraper([])
    data = json.loads(path.read_text())
    stores = data.get("shopify_stores") or []
    return ShopifyScraper(stores)
