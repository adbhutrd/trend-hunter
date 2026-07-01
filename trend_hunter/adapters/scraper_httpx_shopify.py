"""First scraper adapter — Shopify public /products.json (no auth, no key).

Implements core.ports.Scraper for one source only: Shopify.
Other sources (Reddit, Meta ADL, TikTok, Google Trends, AliExpress)
will live in sibling files; this is the proven, tested backbone.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import httpx
from loguru import logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from trend_hunter.core.types import Health, HealthState, RawSignal


class ShopifyScraper:
    name = "shopify"

    def __init__(
        self,
        stores: list[str],
        *,
        user_agent: str = "trend-hunter/0.1 (anonwiz)",
        max_concurrent: int = 2,
        request_jitter_s: tuple[int, int] = (1, 3),
        timeout_s: float = 20.0,
    ) -> None:
        self.stores = stores
        self.user_agent = user_agent
        self._sem = asyncio.Semaphore(max(1, max_concurrent))
        self._jitter = request_jitter_s
        self.timeout_s = timeout_s

    # ── Scraper protocol ───────────────────────────────────────────────────
    async def fetch(self) -> list[RawSignal]:
        signals: list[RawSignal] = []
        now = datetime.now(UTC)
        async with httpx.AsyncClient(
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        ) as client:
            tasks = [self._fetch_store(client, s, now) for s in self.stores]
            for coro in asyncio.as_completed(tasks):
                try:
                    signals.extend(await coro)
                except Exception as e:                                # noqa: BLE001
                    logger.warning(f"shopify: store task failed: {type(e).__name__}: {e}")
        logger.info(f"shopify: ingested {len(signals)} products across {len(self.stores)} stores")
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

    # ── internals ──────────────────────────────────────────────────────────
    async def _fetch_store(
        self,
        client: httpx.AsyncClient,
        url: str,
        run_ts: datetime,
    ) -> list[RawSignal]:
        async with self._sem:
            await asyncio.sleep(random.uniform(*self._jitter))
            url = url.rstrip("/")
            target = f"{url}/products.json"
            try:
                r = await self._get_with_retry(client, target)
            except Exception as e:                                    # noqa: BLE001
                logger.warning(f"shopify: {target} → {type(e).__name__}: {e}")
                return []

            try:
                data = r.json()
            except json.JSONDecodeError as e:
                logger.warning(f"shopify: {target} → json decode: {e}")
                return []

            products = data.get("products") or []
            return [
                RawSignal(
                    source=self.name,
                    external_id=str(p.get("id") or ""),
                    captured_at=run_ts,
                    payload=p,
                )
                for p in products
                if p.get("id") is not None
            ]

    async def _get_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> httpx.Response:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=2, max=20),
                retry=retry_if_exception_type(
                    (httpx.HTTPError, httpx.TimeoutException),
                ),
                reraise=True,
            ):
                with attempt:
                    r = await client.get(url)
                    if r.status_code == 429:
                        # Surfaces as a retryable httpx error
                        raise httpx.HTTPStatusError(
                            "rate-limited",
                            request=r.request,
                            response=r,
                        )
                    r.raise_for_status()
                    return r
        except Exception:
            # defensively re-raise — tenacity's reraise=True already does this,
            # but explicitly returning to the caller keeps the API predictable.
            raise


# ── convenience factory ─────────────────────────────────────────────────────
def from_sources_json(path: Path = Path("./sources.json")) -> ShopifyScraper:
    """Build a ShopifyScraper from sources.json. Empty list if no stores."""
    if not path.exists():
        logger.warning(f"shopify: {path} not found, scraper will be empty")
        return ShopifyScraper([])
    data = json.loads(path.read_text())
    stores = data.get("shopify_stores") or []
    return ShopifyScraper(stores)
