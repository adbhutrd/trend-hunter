"""eBay RSS scraper — fetches real eBay products via public RSS feeds.

Zero auth, zero keys, zero rate limits. eBay RSS endpoints return XML
with prices, links, images, and item IDs for every search query.

Parses the <item> elements from each RSS feed. Price is extracted from
the HTML description using regex. No BeautifulSoup needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

# Rotating User-Agent pool — mimics real browsers.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]

# Regex patterns for extracting price from eBay RSS descriptions
# eBay puts price in <b> tags like: <b>$29.99</b> or <b>US $29.99</b>
_PRICE_RE = re.compile(r"<b>\s*(?:US\s*\$|USD\s*\$?|\$)\s*([\d,]+\.?\d*)\s*</b>", re.IGNORECASE)
_PRICE_RE_FALLBACK = re.compile(r"(?:US\s*\$|USD\s*\$?|\$)\s*([\d,]+\.?\d*)", re.IGNORECASE)

# eBay item ID from link or GUID
_ITEM_ID_RE = re.compile(r"/(\d{12})(?:\?|$)")


def _extract_price(description: str) -> float | None:
    """Extract price from eBay RSS description HTML."""
    if not description:
        return None
    m = _PRICE_RE.search(description)
    if not m:
        m = _PRICE_RE_FALLBACK.search(description)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except (ValueError, TypeError):
            return None
    return None


def _extract_item_id(link: str, guid: str) -> str | None:
    """Extract 12-digit eBay item ID from link or GUID."""
    for text in (guid, link):
        if not text:
            continue
        m = _ITEM_ID_RE.search(text)
        if m:
            return m.group(1)
    return None


class EbayRssScraper:
    """Scrape eBay products via public RSS feeds — zero authentication required.

    Example RSS URL:
        https://www.ebay.com/sch/i.html?_rss=1&_nkw=iphone+case&_sop=10&LH_BIN=1
    """

    name = "ebay"

    def __init__(
        self,
        queries: list[str],
        *,
        max_items_per_query: int = 60,
        timeout_s: float = 15.0,
        concurrency: int = 3,
    ) -> None:
        self.queries = queries or []
        self.max_items_per_query = max(1, max_items_per_query)
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(max(1, concurrency))

    # ── fetch ────────────────────────────────────────────────────────────────
    async def fetch(self) -> list[RawSignal]:
        if not self.queries:
            logger.warning("ebay-rss: no search queries configured")
            return []

        signals: list[RawSignal] = []
        run_ts = datetime.now(UTC)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
            follow_redirects=True,
        ) as client:
            tasks = [self._fetch_query(client, q, run_ts) for q in self.queries]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"ebay-rss: '{self.queries[i]}' failed: {result}")
                else:
                    signals.extend(result)

        logger.info(
            f"ebay-rss: {len(signals)} products across {len(self.queries)} queries"
        )
        return signals

    async def health(self) -> Health:
        ok = bool(self.queries)
        return Health(
            source=self.name,
            state=HealthState.OK if ok else HealthState.UNKNOWN,
            last_run=None,
            last_ok=None,
            rows_in=0,
            error_rate=0.0 if ok else 1.0,
            detail=f"{len(self.queries)} RSS search queries",
        )

    async def _fetch_query(
        self,
        client: httpx.AsyncClient,
        query: str,
        run_ts: datetime,
    ) -> list[RawSignal]:
        """Fetch RSS feed for a single search query and parse all items."""
        async with self._sem:
            # Polite random delay between queries
            await asyncio.sleep(random.uniform(0.5, 1.5))

            url = (
                "https://www.ebay.com/sch/i.html"
                f"?_rss=1"
                f"&_nkw={quote_plus(query)}"
                f"&_sop=10"         # sort: newly listed
                f"&LH_BIN=1"        # Buy It Now only
                f"&LH_PrefLoc=1"    # US sellers preferred
            )

            ua = random.choice(_USER_AGENTS)
            headers = {
                "User-Agent": ua,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            }

            try:
                r = await client.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                logger.debug(f"ebay-rss: '{query}' connection error: {exc}")
                return []

            if r.status_code == 429:
                logger.debug(f"ebay-rss: '{query}' rate-limited — backing off")
                await asyncio.sleep(10.0)
                try:
                    r = await client.get(url, headers=headers)
                except Exception:
                    return []
            elif r.status_code != 200:
                logger.debug(f"ebay-rss: '{query}' HTTP {r.status_code}")
                return []

            # Parse XML
            try:
                root = ET.fromstring(r.text)
            except ET.ParseError as exc:
                logger.debug(f"ebay-rss: '{query}' bad XML: {exc}")
                return []

            # Extract <item> elements from RSS channel
            ns = {"rss": "http://purl.org/rss/1.0/"}
            items = root.findall(".//item")
            if not items:
                items = root.findall(".//rss:item", ns)

            signals: list[RawSignal] = []
            count = 0

            for item_elem in items:
                if count >= self.max_items_per_query:
                    break

                title_elem = item_elem.find("title")
                link_elem = item_elem.find("link")
                guid_elem = item_elem.find("guid")
                desc_elem = item_elem.find("description")

                title = title_elem.text if title_elem is not None else None
                link = link_elem.text if link_elem is not None else None
                guid = guid_elem.text if guid_elem is not None else None
                description = desc_elem.text if desc_elem is not None else ""

                if not title or not link:
                    continue

                # Extract item ID for dedup
                item_id = _extract_item_id(link, guid or "")
                if not item_id:
                    # Fallback: deterministic hash for stable ID across runs
                    item_id = hashlib.md5(link.encode()).hexdigest()[:12]

                price = _extract_price(description)

                payload = {
                    "title": title.strip(),
                    "price": price,
                    "currency": "USD",
                    "url": link.strip(),
                    "description": description[:200] if description else "",
                    "search_query": query,
                    "marketplace": "EBAY_US",
                    "source_type": "rss",
                }

                signals.append(
                    RawSignal(
                        source=self.name,
                        external_id=item_id,
                        captured_at=run_ts,
                        payload=payload,
                    )
                )
                count += 1

            if count:
                logger.debug(f"ebay-rss: '{query}' → {count} items")
            return signals


def from_sources_json(
    path: Path = Path("./sources.json"),
) -> EbayRssScraper:
    """Build an EbayRssScraper from sources.json config.

    Reads ``ebay_rss_queries`` (list of search strings) from sources.json.
    Falls back to ``ebay_search_queries`` if the new key is absent.
    """
    if not path.exists():
        logger.warning("ebay-rss: {} not found", path)
        return EbayRssScraper([])

    data = json.loads(path.read_text())
    queries = data.get("ebay_rss_queries") or data.get("ebay_search_queries") or []
    return EbayRssScraper(queries)
