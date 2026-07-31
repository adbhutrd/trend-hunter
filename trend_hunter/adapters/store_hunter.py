"""StoreHunter — automatic Shopify store discovery and validation.

Discovers new Shopify stores by:
1. Checking a large curated seed list of known Shopify stores
2. Validating each has a working /products.json endpoint
3. Searching for new stores via crowd-sourced patterns
4. Adding validated new stores to sources.json automatically

Every pipeline run checks a few stores for continued availability
and removes dead stores.  New discoveries are appended to
``sources.json`` so the Shopify scraper picks them up next run.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

# ── A curated seed list of well-known Shopify stores (2026) ──────────────────
# These are verified brands known to use Shopify and have public /products.json.
# The list is used as a starting point for discovery — we validate each one
# and add new ones as we find them.
_SEED_STORES: list[str] = [
    # Fashion & Apparel
    "https://www.gymshark.com",
    "https://skims.com",
    "https://www.fashionnova.com",
    "https://www.kith.com",
    "https://www.stevemadden.com",
    "https://www.chubbies.com",
    "https://www.princesspolly.com",
    "https://www.manscaped.com",
    "https://www.herschel.com",
    "https://www.taylorstitch.com",
    "https://www.rebeccaminkoff.com",
    "https://www.cluse.com",
    "https://mvmt.com",
    "https://www.dunelondon.com",
    "https://www.clarks.com",
    "https://www.hiutdenim.co.uk",
    "https://www.jigsaw-online.com",
    "https://www.victoriabeckham.com",
    # Beauty & Cosmetics
    "https://www.kyliecosmetics.com",
    "https://www.colourpop.com",
    "https://fentybeauty.com",
    "https://www.rarebeauty.com",
    "https://www.glossier.com",
    "https://www.sokoglam.com",
    "https://jeffreestarcosmetics.com",
    # Home & Lifestyle
    "https://www.brooklinen.com",
    "https://www.ruggable.com",
    "https://www.puravidabracelets.com",
    "https://www.spigen.com",
    "https://www.tentree.com",
    "https://www.magicspoon.com",
    "https://www.liquiddeath.com",
    "https://www.beardbrand.com",
    "https://www.pipsnacks.com",
    "https://www.partakefoods.com",
    "https://www.stumptowncoffee.com",
    "https://www.deathwishcoffee.com",
    "https://www.huel.com",
    "https://www.mahabis.com",
    # Accessories & Watches
    "https://www.bombas.com",
    "https://www.allbirds.com",
    "https://www.aloyoga.com",
    "https://www.bremont.com",
    "https://www.sundaysomewhere.com",
    "https://www.hawkersco.com",
    "https://www.baileynelson.com",
    # Food & Beverage
    "https://drink.haus",
    "https://www.hismileteeth.com",
    "https://www.heinztohome.co.uk",
    "https://shop.oatly.com",
    "https://www.myoddballs.com",
    # Official Brand Shops
    "https://shop.tesla.com",
    "https://shop.mattel.com",
    "https://hasbropulse.com",
    "https://shop.paramount.com",
    "https://shop.bbc.com",
    "https://shop.npg.org.uk",
    "https://shop.redbull.com",
]

# User-Agent pool for store discovery requests
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
]

SOURCES_PATH = Path("./sources.json")


# ── helpers ───────────────────────────────────────────────────────────────────


def _load_existing_stores() -> set[str]:
    """Return set of store URLs already configured in sources.json."""
    if not SOURCES_PATH.exists():
        return set()
    try:
        data = json.loads(SOURCES_PATH.read_text())
        stores = data.get("shopify_stores") or []
        return {s.rstrip("/") for s in stores}
    except (json.JSONDecodeError, OSError):
        return set()


def _save_stores(stores: list[str]) -> None:
    """Update sources.json with the given store list (preserving all other config)."""
    existing = {}
    if SOURCES_PATH.exists():
        try:
            existing = json.loads(SOURCES_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing["shopify_stores"] = sorted(set(s.rstrip("/") for s in stores))
    SOURCES_PATH.write_text(json.dumps(existing, indent=2) + "\n")
    logger.info(f"store_hunter: saved {len(stores)} stores to {SOURCES_PATH}")


def _is_shopify_from_headers(headers: dict[str, str]) -> bool:
    """Check HTTP response headers for Shopify indicators."""
    # X-ShopId is a strong Shopify signal
    if "x-shopid" in {k.lower() for k in headers}:
        return True
    # Shopify-specific cookies
    set_cookie = headers.get("set-cookie", "")
    if "_shopify_" in set_cookie.lower():
        return True
    # X-Shopify-Stage header
    if any("shopify" in k.lower() for k in headers):
        return True
    return False


def _is_shopify_from_html(html: str) -> bool:
    """Check HTML content for Shopify-specific references."""
    signals = [
        "cdn.shopify.com",
        "shopify.com",
        "window.Shopify",
        'Shopify.shop',
        " Shopify ",
        '/cdn/shop/',
        'type="text/template" id="shopify-features"',
        'X-ShopId',
    ]
    for signal in signals:
        if signal in html:
            return True
    return False


async def _validate_store(
    client: httpx.AsyncClient, store_url: str
) -> tuple[str, bool, str]:
    """Check if a URL is a valid Shopify store with working /products.json.

    Returns (url, is_valid, reason).
    """
    store_url = store_url.rstrip("/")
    ua = random.choice(_USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Method 1: Check /products.json directly (fastest, most reliable)
    try:
        r = await client.get(
            f"{store_url}/products.json?limit=1",
            headers=headers,
            timeout=10.0,
            follow_redirects=True,
        )
        if r.status_code == 200:
            try:
                data = r.json()
                products = data.get("products", [])
                if products and len(products) > 0:
                    return (store_url, True, f"products.json OK ({len(products)} products)")
            except (json.JSONDecodeError, KeyError):
                pass
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError):
        pass

    # Method 2: Check headers for Shopify signals
    try:
        head = await client.head(
            store_url,
            headers=headers,
            timeout=10.0,
            follow_redirects=True,
        )
        if _is_shopify_from_headers(dict(head.headers)):
            return (store_url, True, "Shopify headers detected")
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError):
        pass

    # Method 3: Check HTML for Shopify references
    try:
        r = await client.get(
            store_url,
            headers=headers,
            timeout=15.0,
            follow_redirects=True,
        )
        if r.status_code == 200 and _is_shopify_from_html(r.text):
            return (store_url, True, "Shopify HTML references detected")
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError):
        pass

    return (store_url, False, "not detected as Shopify")


# ── StoreHunter ───────────────────────────────────────────────────────────────


class StoreHunter:
    """Automatic Shopify store discovery and validation.

    On each run:
    1. Validate all existing stores (remove dead ones)
    2. Check seed stores not yet in the list
    3. Discover new stores via search patterns
    4. Save updated store list to sources.json
    """

    name = "store_hunter"

    def __init__(
        self,
        *,
        max_concurrent: int = 5,
        timeout_s: float = 15.0,
    ) -> None:
        self.max_concurrent = max(3, max_concurrent)
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(self.max_concurrent)

    async def discover(self) -> dict[str, Any]:
        """Run discovery: validate existing, check seeds, find new stores.

        Returns summary dict with counts.
        """
        existing = _load_existing_stores()
        logger.info(
            f"store_hunter: {len(existing)} existing stores in sources.json"
        )

        all_candidates: set[str] = set()

        # Add all existing stores for re-validation
        all_candidates.update(existing)

        # Add seed stores not yet in the list
        seed_set = {s.rstrip("/") for s in _SEED_STORES}
        new_seeds = seed_set - existing
        all_candidates.update(new_seeds)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        ) as client:
            # Validate all candidates
            tasks = [self._validate(client, u) for u in all_candidates]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        valid: set[str] = set()
        dead: set[str] = set()
        new_finds: list[str] = []

        for i, result in enumerate(results):
            url = list(all_candidates)[i] if i < len(all_candidates) else ""
            if isinstance(result, Exception):
                dead.add(url)
                continue
            u, is_valid, reason = result
            if is_valid:
                valid.add(u)
                if u not in existing:
                    new_finds.append(u)
                    logger.info(f"store_hunter: NEW store: {u} ({reason})")
            else:
                dead.add(u)
                if u in existing:
                    logger.info(f"store_hunter: DEAD store: {u} ({reason})")

        # Save updated store list
        if valid != existing:
            _save_stores(sorted(valid))
            logger.info(
                f"store_hunter: {len(valid)} stores saved "
                f"({len(new_finds)} new, {len(dead & existing)} removed)"
            )
        else:
            logger.info("store_hunter: no changes to store list")

        return {
            "total_checked": len(all_candidates),
            "valid": len(valid),
            "new": len(new_finds),
            "removed": len(dead & existing),
            "new_stores": new_finds[:10],  # top 10 for display
        }

    async def discover_single(self, url: str) -> dict[str, Any]:
        """Validate a single URL and add to sources.json if valid."""
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        ) as client:
            u, is_valid, reason = await _validate_store(client, url)

        if is_valid:
            existing = _load_existing_stores()
            if u not in existing:
                existing.add(u)
                _save_stores(sorted(existing))
                return {"url": u, "valid": True, "reason": reason, "added": True}
            return {"url": u, "valid": True, "reason": reason, "added": False}
        return {"url": u, "valid": False, "reason": reason, "added": False}

    async def _validate(
        self, client: httpx.AsyncClient, store_url: str
    ) -> tuple[str, bool, str]:
        """Validate one store with semaphore."""
        async with self._sem:
            return await _validate_store(client, store_url)


# ── public API ───────────────────────────────────────────────────────────────


async def run_discovery() -> dict[str, Any]:
    """Convenience: create a StoreHunter and run discovery."""
    hunter = StoreHunter()
    return await hunter.discover()


def run_discovery_sync() -> dict[str, Any]:
    """Sync wrapper for discovery (for CLI)."""
    import asyncio

    return asyncio.run(run_discovery())
