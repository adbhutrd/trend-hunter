"""Reddit trending scraper — public JSON, no auth, no rate limits.

Uses Reddit's public .json endpoint (add .json to any subreddit URL).
No API key, no OAuth, no registration required.

Implements core.ports.Scraper so it slots straight into the ingest runner.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime

import httpx
from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

HEADERS = {
    "User-Agent": "trend-hunter/0.1 (anonwiz; +https://github.com/)",
}


class RedditScraper:
    name = "reddit"

    def __init__(
        self,
        subreddits: list[str],
        *,
        limit_per_sub: int = 50,
        timeout_s: float = 20.0,
    ) -> None:
        self.subreddits = subreddits
        self.limit_per_sub = limit_per_sub
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
            for sub in self.subreddits:
                try:
                    batch = await self._fetch_subreddit(client, sub, now)
                    signals.extend(batch)
                    await asyncio.sleep(1.0)  # Reddit rate limit: ~60 req/min
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"reddit: r/{sub} failed: {type(e).__name__}: {e}")
        logger.info(f"reddit: ingested {len(signals)} posts across {len(self.subreddits)} subs")
        return signals

    async def health(self) -> Health:
        ok = bool(self.subreddits)
        return Health(
            source=self.name,
            state=HealthState.OK if ok else HealthState.UNKNOWN,
            last_run=None,
            last_ok=None,
            rows_in=0,
            error_rate=0.0,
            detail=f"{len(self.subreddits)} subreddit(s) configured",
        )

    # ── internals ──────────────────────────────────────────────────────────
    async def _fetch_subreddit(
        self,
        client: httpx.AsyncClient,
        subreddit: str,
        run_ts: datetime,
    ) -> list[RawSignal]:
        url = f"https://www.reddit.com/r/{subreddit}/hot.json"
        params = {"limit": str(min(self.limit_per_sub, 100))}
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"reddit: r/{subreddit} fetch failed: {e}")
            return []

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            logger.warning(f"reddit: r/{subreddit} JSON decode failed: {e}")
            return []

        children = data.get("data", {}).get("children", [])
        signals: list[RawSignal] = []
        for child in children:
            post = child.get("data", {})
            post_id = post.get("id", "")
            if not post_id:
                continue

            title = post.get("title", "")
            selftext = post.get("selftext", "")[:500]
            score = post.get("score", 0)
            num_comments = post.get("num_comments", 0)
            permalink = "https://reddit.com" + post.get("permalink", "")
            created_utc = post.get("created_utc", 0)

            # Extract potential product mentions and prices from the post text.
            full_text = f"{title}\n{selftext}"
            prices = _extract_prices(full_text)
            avg_price = sum(prices) / len(prices) if prices else None

            signals.append(
                RawSignal(
                    source=self.name,
                    external_id=post_id,
                    captured_at=run_ts,
                    payload={
                        "title": title,
                        "selftext": selftext,
                        "score": score,
                        "num_comments": num_comments,
                        "url": permalink,
                        "subreddit": subreddit,
                        "created_utc": created_utc,
                        "price": avg_price,
                        "currency": "USD",
                        "mentioned_prices": prices,
                    },
                )
            )

        return signals


def _extract_prices(text: str) -> list[float]:
    """Extract dollar prices mentioned in post text.

    Looks for patterns like $49.99, $100, USD 29.99, etc.
    """
    prices: list[float] = []
    for match in re.finditer(r"\$([\d,]+\.?\d{0,2})", text):
        try:
            prices.append(float(match.group(1).replace(",", "")))
        except ValueError:
            continue
    return prices
