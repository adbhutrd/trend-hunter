"""HackerNews API scraper — trending tech products and discussions.

Uses the official Firebase API (no auth, no rate limits, free forever).
Shows trending startups, products, and tech discussions — perfect for
spotting emerging product trends before they hit mainstream commerce.

API docs: https://github.com/HackerNews/API
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
HEADERS = {
    "User-Agent": "trend-hunter/0.1 (anonwiz)",
    "Accept": "application/json",
}

# Keywords that signal product-related posts (launches, tools, startups).
PRODUCT_KEYWORDS = [
    "launch", "product", "startup", "tool", "app", "saas",
    "github.com", "demo", "beta", "released", "shipping",
    "built", "made", "created", "introducing", "announcing",
]


class HackerNewsScraper:
    name = "hackernews"

    def __init__(
        self,
        *,
        top_n: int = 30,
        max_items: int = 20,
        timeout_s: float = 20.0,
    ) -> None:
        self.top_n = top_n
        self.max_items = max_items
        self.timeout_s = timeout_s

    async def fetch(self) -> list[RawSignal]:
        signals: list[RawSignal] = []
        now = datetime.now(UTC)
        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=httpx.Timeout(self.timeout_s),
        ) as client:
            try:
                r = await client.get(HN_TOP_URL)
                r.raise_for_status()
                top_ids = r.json()[:self.top_n]
            except Exception as e:
                logger.warning(f"hackernews: top stories failed: {e}")
                return []

            # Fetch each item concurrently (limited by self.max_items).
            tasks = []
            for item_id in top_ids[:self.max_items]:
                tasks.append(self._fetch_item(client, item_id, now))
            for coro in asyncio.as_completed(tasks):
                try:
                    sig = await coro
                    if sig is not None:
                        signals.append(sig)
                except Exception as e:
                    logger.warning(f"hackernews: item failed: {e}")

        logger.info(f"hackernews: ingested {len(signals)} product-related posts")
        return signals

    async def health(self) -> Health:
        return Health(
            source=self.name,
            state=HealthState.OK,
            last_run=None,
            last_ok=None,
            rows_in=0,
            error_rate=0.0,
            detail=f"top_n={self.top_n} max_items={self.max_items}",
        )

    async def _fetch_item(
        self,
        client: httpx.AsyncClient,
        item_id: int,
        run_ts: datetime,
    ) -> RawSignal | None:
        try:
            r = await client.get(HN_ITEM_URL.format(item_id))
            r.raise_for_status()
            item = r.json()
        except Exception:
            return None

        if item is None or item.get("type") != "story":
            return None

        title = item.get("title", "")
        url = item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
        score = item.get("score", 0)
        descendants = item.get("descendants", 0)
        text = item.get("text", "")[:500]

        # Only keep posts that look product-related.
        full_text = f"{title} {text} {url}".lower()
        is_product = any(kw in full_text for kw in PRODUCT_KEYWORDS)
        if not is_product:
            return None

        return RawSignal(
            source=self.name,
            external_id=str(item_id),
            captured_at=run_ts,
            payload={
                "title": title,
                "url": url,
                "score": score,
                "comments": descendants,
                "text": text,
                "price": float(score),  # proxy: engagement as "price"
                "currency": "POINTS",
            },
        )
