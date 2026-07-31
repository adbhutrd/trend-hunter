"""Etsy scraper — uses Playwright stealth mode (via web-scraping skill).

Etsy blocks simple HTTP requests (403). This adapter uses the web-scraping skill's
recommended cascade: Playwright + playwright-stealth → JSON-LD extraction.

Key improvements over basic scrapers:
  - Single browser instance reused across all search queries
  - playwright-stealth patches navigator.webdriver, plugins, languages, WebGL
  - 3-5 second random delays between searches (Etsy rate-limits aggressively)
  - JSON-LD structured data extraction (most stable)
  - Graceful fallback if Playwright/Chromium not available
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

# playwright-stealth for anti-bot bypass (installed via pip)
try:
    from playwright_stealth import stealth_async as _stealth_async
except ImportError:
    _stealth_async = None

DEFAULT_SEARCHES = [
    "trending gifts",
    "popular handmade",
    "bestseller home decor",
    "personalized jewelry",
    "vintage fashion",
    "wall art popular",
    "unique presents",
    "custom made gifts",
    "trending accessories",
    "eco friendly gifts",
]

ETSY_SEARCH_URL = "https://www.etsy.com/search"


class EtsyScraper:
    name = "etsy"

    def __init__(
        self,
        searches: list[str] | None = None,
        *,
        max_per_search: int = 20,
        timeout_s: float = 30.0,
    ) -> None:
        self.searches = searches or DEFAULT_SEARCHES
        self.max_per_search = max_per_search
        self.timeout_s = timeout_s

    async def fetch(self) -> list[RawSignal]:
        signals: list[RawSignal] = []
        now = datetime.now(UTC)

        # Verify Playwright is available
        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError:
            logger.warning("etsy: playwright not installed — run: pip install playwright && playwright install chromium")
            return []

        async with _PlaywrightManager() as manager:
            for search_query in self.searches:
                try:
                    batch = await self._fetch_search(manager, search_query, now)
                    signals.extend(batch)
                    logger.info(f"etsy: '{search_query}' → {len(batch)} products")
                except Exception as e:
                    logger.warning(f"etsy: '{search_query}' failed: {type(e).__name__}: {e}")

                # Polite delay between searches
                await asyncio.sleep(random.uniform(3.0, 5.0))

        logger.info(f"etsy: total {len(signals)} products across {len(self.searches)} searches")
        return signals

    async def health(self) -> Health:
        ok = bool(self.searches)
        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError:
            ok = False
        return Health(
            source=self.name,
            state=HealthState.OK if ok else HealthState.FAILING,
            last_run=None,
            last_ok=None,
            rows_in=0,
            error_rate=0.0,
            detail=f"{len(self.searches)} search queries, playwright={'ok' if ok else 'missing'}",
        )

    async def _fetch_search(
        self,
        manager: _PlaywrightManager,
        search_query: str,
        run_ts: datetime,
    ) -> list[RawSignal]:
        """Fetch products for one search query using Playwright stealth."""
        url = f"{ETSY_SEARCH_URL}?q={search_query.replace(' ', '+')}"

        page = await manager.get_page()
        signals = []

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_s * 1000)
            await page.wait_for_timeout(4000)  # Let JS render listings
        except Exception as e:
            logger.debug(f"etsy: playwright goto '{search_query}' failed: {e}")
            return signals

        # Strategy 1: Extract JSON-LD structured data (most reliable)
        signals = await self._extract_jsonld(page, search_query, run_ts)

        # Strategy 2: If JSON-LD gave nothing, try parsing HTML
        if not signals:
            try:
                html = await page.content()
                signals = self._parse_html(html, search_query, run_ts)
            except Exception:
                pass

        return signals[: self.max_per_search]

    async def _extract_jsonld(
        self, page: Any, search_query: str, run_ts: datetime
    ) -> list[RawSignal]:
        """Extract products from JSON-LD structured data.

        Handles both:
          - ItemList (search pages) with itemListElement containing Products
          - Single Product items (product detail pages)
        """
        try:
            jsonld_raw = await page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll(
                        'script[type="application/ld+json"]'
                    );
                    return Array.from(scripts).map(s => s.textContent);
                }
            """)
        except Exception:
            return []

        signals = []
        seen_ids = set()

        def _process_item(item: dict) -> RawSignal | None:
            """Process a single JSON-LD item, recursing into ItemList."""
            if not isinstance(item, dict):
                return None

            item_type = item.get("@type", "")

            # Handle ItemList: recurse into each element
            if "ItemList" in item_type:
                for elem in item.get("itemListElement", []):
                    if isinstance(elem, dict):
                        sub_item = elem.get("item", elem)
                        sig = _process_item(sub_item)
                        if sig:
                            signals.append(sig)
                return None

            # Handle Product items
            if "Product" in item_type:
                return self._jsonld_to_signal(item, search_query, run_ts)

            return None

        for raw in jsonld_raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            items = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])

            for item in items:
                sig = _process_item(item)
                if sig and sig.external_id not in seen_ids:
                    seen_ids.add(sig.external_id)
                    signals.append(sig)

        return signals

    def _parse_html(self, html: str, search_query: str, run_ts: datetime) -> list[RawSignal]:
        """Fallback: parse product listings from page HTML."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        signals = []
        seen_ids = set()

        # Try to find listing cards
        for selector in ["div[data-listing-id]", "div.v2-listing-card", "li.wt-list-unstyled"]:
            items = soup.select(selector)
            if not items:
                continue

            for item in items[: self.max_per_search]:
                listing_id = item.get("data-listing-id", "")
                if not listing_id:
                    link = item.select_one("a[href*='/listing/']")
                    if link:
                        match = re.search(r"/listing/(\d+)", link.get("href", ""))
                        listing_id = match.group(1) if match else ""

                if not listing_id:
                    listing_id = f"etsy-{hash(str(item)) & 0xFFFFFFFF}"

                if listing_id in seen_ids:
                    continue
                seen_ids.add(listing_id)

                title_el = (
                    item.select_one("h3")
                    or item.select_one(".v2-listing-card__title")
                    or item.select_one("[data-search-results-title]")
                    or item.select_one("a[title]")
                )
                title = title_el.get_text(strip=True) if title_el else "Etsy Item"

                link_el = item.select_one("a[href*='/listing/']") or item.select_one("a")
                url = ""
                if link_el:
                    href = link_el.get("href", "")
                    url = f"https://www.etsy.com{href}" if href.startswith("/") else href

                price_el = item.select_one(".currency-value, .wt-text-title-01, [data-price]")
                price = None
                if price_el:
                    try:
                        text = price_el.get_text(strip=True).replace(",", "").replace("$", "")
                        price = float(text) if text else None
                    except (ValueError, TypeError):
                        pass

                signals.append(
                    RawSignal(
                        source=self.name,
                        external_id=str(listing_id),
                        captured_at=run_ts,
                        payload={
                            "title": title[:200],
                            "price": price,
                            "currency": "USD",
                            "url": url,
                            "search_query": search_query,
                        },
                    )
                )
            if signals:
                break

        return signals

    def _jsonld_to_signal(
        self, item: dict, search_query: str, run_ts: datetime
    ) -> RawSignal | None:
        name = item.get("name", "")
        if not name:
            return None

        offers = item.get("offers", {})
        price_str = ""
        if isinstance(offers, dict):
            price_str = offers.get("price", "")
        elif isinstance(offers, list):
            price_str = offers[0].get("price", "") if offers else ""

        price = None
        if price_str:
            try:
                price = float(price_str)
            except (ValueError, TypeError):
                pass

        url = item.get("url", "")
        if url and url.startswith("/"):
            url = f"https://www.etsy.com{url}"

        ext_id = item.get("sku", item.get("mpn", "")) or f"etsy-{hash(name + url) & 0xFFFFFFFF}"

        return RawSignal(
            source=self.name,
            external_id=str(ext_id),
            captured_at=run_ts,
            payload={
                "title": str(name)[:200],
                "price": price,
                "currency": offers.get("priceCurrency", "USD") if isinstance(offers, dict) else "USD",
                "url": url,
                "description": (item.get("description", "") or "")[:500],
                "image": item.get("image", "") if isinstance(item.get("image"), str) else "",
                "search_query": search_query,
            },
        )


class _PlaywrightManager:
    """Context manager that keeps one browser instance for all searches."""

    def __init__(self):
        self.browser = None
        self._page = None
        self._context = None
        self._pw = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        return self

    async def get_page(self):
        """Create a fresh page with stealth applied. Closes previous page if any."""
        if not self.browser:
            raise RuntimeError("Browser not initialized")

        # Close previous page to avoid memory leak
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass

        self._context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        self._page = await self._context.new_page()
        # Apply comprehensive stealth if available
        if _stealth_async:
            await _stealth_async(self._page)
        return self._page

    async def __aexit__(self, *args):
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()


