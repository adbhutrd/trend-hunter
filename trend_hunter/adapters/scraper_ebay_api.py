"""eBay Browse API scraper — searches eBay marketplace for trending products.

Uses OAuth2 client_credentials flow (Application Access Token).
No user login needed. Token auto-refreshes on expiry.
Search queries configured via sources.json → ebay_search_queries.

Rate limit: ~5,000 API calls/day on free tier.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

# ── eBay API endpoints ─────────────────────────────────────────────────────
_OAUTH_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_OAUTH_TOKEN_URL_SANDBOX = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
_BROWSE_BASE = "https://api.ebay.com/buy/browse/v1/"
_BROWSE_BASE_SANDBOX = "https://api.sandbox.ebay.com/buy/browse/v1/"
_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayApiScraper:
    """Scrape eBay via the official Browse API (OAuth2 client_credentials)."""

    name = "ebay"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        search_queries: list[str] | None = None,
        sandbox: bool = False,
        max_results_per_query: int = 200,
        max_pages: int = 3,
        timeout_s: float = 15.0,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.sandbox = sandbox
        self.search_queries = search_queries or []
        self.max_results_per_query = max(1, min(max_results_per_query, 200))
        self.max_pages = max(1, max_pages)
        self.timeout_s = timeout_s

        # Token cache
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ── helpers ──────────────────────────────────────────────────────────────
    @property
    def _token_url(self) -> str:
        return _OAUTH_TOKEN_URL_SANDBOX if self.sandbox else _OAUTH_TOKEN_URL

    @property
    def _browse_base(self) -> str:
        return _BROWSE_BASE_SANDBOX if self.sandbox else _BROWSE_BASE

    def _auth_header(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}"
        return base64.b64encode(raw.encode()).decode()

    # ── OAuth ─────────────────────────────────────────────────────────────────
    async def _get_token(self, client: httpx.AsyncClient) -> str:
        """Get or refresh an Application Access Token."""
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token

        resp = await client.post(
            self._token_url,
            headers={
                "Authorization": f"Basic {self._auth_header()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": _OAUTH_SCOPE,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"eBay OAuth failed ({resp.status_code}): {resp.text[:500]}"
            )

        data: dict[str, Any] = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + float(data.get("expires_in", 7200))
        logger.info("ebay: OAuth token obtained (expires in {}s)", int(data.get("expires_in", 7200)))
        return self._token

    # ── search ────────────────────────────────────────────────────────────────
    async def _search(
        self,
        client: httpx.AsyncClient,
        token: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Execute a single eBay search, returning paginated item summaries."""
        url = f"{self._browse_base}item_summary/search"
        all_items: list[dict[str, Any]] = []
        offset = 0

        for _page in range(self.max_pages):
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                    "Accept": "application/json",
                },
                params={
                    "q": query,
                    "limit": limit,
                    "offset": offset,
                },
            )

            if resp.status_code == 429:
                logger.warning("ebay: rate-limited — backing off 10s")
                await asyncio.sleep(10.0)
                continue

            if resp.status_code != 200:
                logger.warning(
                    f"ebay: search '{query}' HTTP {resp.status_code}: {resp.text[:200]}"
                )
                break

            data: dict[str, Any] = resp.json()
            items = data.get("itemSummaries") or []
            if not items:
                break

            all_items.extend(items)

            total = int(data.get("total", 0))
            if offset + limit >= total:
                break
            offset += limit

            # Polite delay between pages
            await asyncio.sleep(0.3)

        return all_items

    # ── scrape ────────────────────────────────────────────────────────────────
    async def fetch(self) -> list[RawSignal]:
        if not self.search_queries:
            logger.warning("ebay: no search queries configured")
            return []

        signals: list[RawSignal] = []
        run_ts = datetime.now(UTC)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
            follow_redirects=True,
        ) as client:
            token = await self._get_token(client)

            for query in self.search_queries:
                try:
                    items = await self._search(
                        client, token, query, self.max_results_per_query
                    )
                except Exception as exc:
                    logger.warning(f"ebay: search '{query}' failed: {exc}")
                    continue

                for item in items:
                    item_id = item.get("itemId")
                    if not item_id:
                        continue

                    price_info = item.get("price") or {}
                    price_val = price_info.get("value")
                    try:
                        price_val = float(price_val) if price_val is not None else None
                    except (ValueError, TypeError):
                        price_val = None

                    payload = {
                        "title": item.get("title"),
                        "price": price_val,
                        "currency": price_info.get("currency", "USD"),
                        "url": item.get("itemWebUrl") or item.get("itemHref"),
                        "image_url": (
                            item.get("image", {}).get("imageUrl")
                            if isinstance(item.get("image"), dict)
                            else None
                        ),
                        "condition": item.get("condition"),
                        "seller": (
                            item.get("seller", {}).get("username")
                            if isinstance(item.get("seller"), dict)
                            else None
                        ),
                        "category_path": item.get("categoryPath"),
                        "marketplace": "EBAY_US",
                        "search_query": query,
                    }

                    signals.append(
                        RawSignal(
                            source=self.name,
                            external_id=str(item_id),
                            captured_at=run_ts,
                            payload=payload,
                        )
                    )

                await asyncio.sleep(0.5)

        logger.info(f"ebay: {len(signals)} products across {len(self.search_queries)} queries")
        return signals

    async def health(self) -> Health:
        ok = bool(self.search_queries)
        return Health(
            source=self.name,
            state=HealthState.OK if ok else HealthState.UNKNOWN,
            last_run=None,
            last_ok=None,
            rows_in=0,
            error_rate=0.0 if ok else 1.0,
            detail=f"{len(self.search_queries)} search queries, sandbox={self.sandbox}",
        )


def from_sources_json(
    path: Path = Path("./sources.json"),
    sandbox: bool = False,
) -> EbayApiScraper:
    """Build an EbayApiScraper from sources.json config.

    Expects `ebay_search_queries` and `ebay_credentials` in sources.json.
    Falls back to environment variables if not in JSON.
    """
    if not path.exists():
        logger.warning("ebay: {} not found, scraper will be empty", path)
        return EbayApiScraper("", "", sandbox=sandbox)

    data = json.loads(path.read_text())

    # Search queries from JSON
    search_queries: list[str] = data.get("ebay_search_queries") or []

    # Credentials: JSON → env fallback
    creds = data.get("ebay_credentials") or {}
    client_id = creds.get("client_id") or ""
    client_secret = creds.get("client_secret") or ""

    # Env var fallback for security (prod keys should never be in JSON)
    client_id = os.getenv("EBAY_CLIENT_ID", client_id)
    client_secret = os.getenv("EBAY_CLIENT_SECRET", client_secret)

    # Auto-detect sandbox from client_id suffix
    if client_id and "SBX" in client_id:
        sandbox = True

    return EbayApiScraper(
        client_id=client_id,
        client_secret=client_secret,
        search_queries=search_queries,
        sandbox=sandbox,
    )
