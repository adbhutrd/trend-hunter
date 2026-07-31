"""eBay RSS scraper — zero API key, zero OAuth, zero rate limits.

Scrapes eBay's public RSS search feeds.  Each eBay search page exposes an
RSS feed at:
  https://www.ebay.com/sch/i.html?_nkw={keyword}&_rss=1

This is a truly open-source data source — no registration, no API key,
no rate limits beyond standard web scraping courtesy.

Implements core.ports.Scraper so it slots straight into the ingest runner.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx
from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

EBAY_RSS_URL = "https://www.ebay.com/sch/i.html"
EBAY_RSS_PARAMS = {
    "_rss": "1",
    "rt": "nc",
    "_sop": "10",  # sort by: newly listed first
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Default search keywords for eBay — broad categories with high listing volume.
DEFAULT_KEYWORDS = [
    "trending",
    "viral",
    "hot item",
    "bestseller",
    "popular",
]


class EbayRssScraper:
    name = "ebay"

    def __init__(
        self,
        keywords: list[str] | None = None,
        *,
        max_per_keyword: int = 50,
        timeout_s: float = 20.0,
    ) -> None:
        self.keywords = keywords or DEFAULT_KEYWORDS
        self.max_per_keyword = max_per_keyword
        self.timeout_s = timeout_s

    # ── Scraper protocol ───────────────────────────────────────────────────
    async def fetch(self) -> list[RawSignal]:
        signals: list[RawSignal] = []
        now = datetime.now(UTC)
        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        ) as client:
            for kw in self.keywords:
                try:
                    batch = await self._fetch_keyword(client, kw, now)
                    signals.extend(batch)
                    await asyncio.sleep(0.5)  # polite delay between keywords
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"ebay: keyword '{kw}' failed: {type(e).__name__}: {e}")
        logger.info(f"ebay: ingested {len(signals)} items across {len(self.keywords)} keywords")
        return signals

    async def health(self) -> Health:
        ok = bool(self.keywords)
        return Health(
            source=self.name,
            state=HealthState.OK if ok else HealthState.UNKNOWN,
            last_run=None,
            last_ok=None,
            rows_in=0,
            error_rate=0.0,
            detail=f"{len(self.keywords)} keyword(s) configured",
        )

    # ── internals ──────────────────────────────────────────────────────────
    async def _fetch_keyword(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        run_ts: datetime,
    ) -> list[RawSignal]:
        params = {**EBAY_RSS_PARAMS, "_nkw": keyword, "_ipg": str(min(self.max_per_keyword, 100))}
        try:
            r = await client.get(EBAY_RSS_URL, params=params)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ebay: RSS fetch failed for '{keyword}': {e}")
            return []

        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            logger.warning(f"ebay: XML parse failed for '{keyword}': {e}")
            return []

        signals: list[RawSignal] = []
        for item_el in root.findall(".//item"):
            title_el = item_el.find("title")
            link_el = item_el.find("link")
            desc_el = item_el.find("description")

            title = title_el.text if title_el is not None else "Unknown"
            link = link_el.text if link_el is not None else ""
            desc = desc_el.text if desc_el is not None else ""

            item_id = _extract_item_id(link) or f"ebay-{hash(title) & 0xFFFFFF}"
            price = _parse_price(desc)

            signals.append(
                RawSignal(
                    source=self.name,
                    external_id=str(item_id),
                    captured_at=run_ts,
                    payload={
                        "title": title,
                        "price": price,
                        "currency": "USD",
                        "url": link,
                        "keyword": keyword,
                        "description": desc[:500],
                    },
                )
            )

        return signals


def _extract_item_id(url: str) -> str | None:
    """Pull the eBay item ID from a listing URL."""
    if not url:
        return None
    match = re.search(r"/itm/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"item=(\d+)", url)
    if match:
        return match.group(1)
    return None


def _parse_price(desc_html: str) -> float | None:
    """Extract the first dollar price from eBay RSS description HTML."""
    if not desc_html:
        return None
    match = re.search(r"\$([\d,]+\.?\d*)", desc_html)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None
