"""Ingest runner — Shopify + eBay. Orchestrates scrapers and persists results.

Five reliability principles in code:
  * idempotent   → upsert ON CONFLICT DO NOTHING  (composite PKs in Storage)
  * observable   → health.row written per source, every run  (consumed by doctor)
  * isolated     → per-task exception capture so one failing source never sinks the run
  * bounded       → Semaphore limits per-scraper concurrency
  * self-testing  → doctor() reads the same health table this runner writes
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.ports import Scraper
from trend_hunter.core.types import HealthState, RawSignal


# ── health upsert (ON CONFLICT DO UPDATE — generic upsert uses DO NOTHING) ──
def _upsert_health(
    storage: DuckDBStorage,
    source: str,
    state: str,
    last_run: datetime,
    last_ok: datetime | None,
    rows_in: int,
    error_rate: float,
    detail: str,
) -> None:
    """Upsert a health row — always updates, never ignores."""
    storage.execute(
        """
        INSERT INTO health (source, state, last_run, last_ok, rows_in, error_rate, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source) DO UPDATE SET
            state       = excluded.state,
            last_run    = excluded.last_run,
            last_ok     = COALESCE(excluded.last_ok, health.last_ok),
            rows_in     = excluded.rows_in,
            error_rate  = excluded.error_rate,
            detail      = excluded.detail
        """,
        (source, state, last_run, last_ok, rows_in, error_rate, detail),
    )


# ── registration ─────────────────────────────────────────────────────────────
def default_scrapers() -> list[Scraper]:
    """Build the default scraper set — Shopify + eBay API."""
    from trend_hunter.adapters.scraper_ebay_api import from_sources_json as ebay_from_sources
    from trend_hunter.adapters.scraper_httpx_shopify import from_sources_json

    scrapers: list[Scraper] = [from_sources_json(Path("./sources.json"))]

    # eBay API scraper — OAuth2, 5k calls/day, real products with production keys
    try:
        ebay = ebay_from_sources(Path("./sources.json"))
        if ebay.search_queries:
            scrapers.append(ebay)
            logger.info(f"runner: eBay API scraper activated (sandbox={ebay.sandbox})")
    except Exception as e:
        logger.debug(f"runner: eBay API scraper skipped ({e})")

    return scrapers


async def _run_store_discovery() -> None:
    """Run StoreHunter to discover new Shopify stores and update sources.json."""
    try:
        from trend_hunter.adapters.store_hunter import StoreHunter
        hunter = StoreHunter()
        result = await hunter.discover()
        if result.get("new", 0) > 0:
            logger.info(f"store_hunter: {result['new']} new stores discovered")
        if result.get("removed", 0) > 0:
            logger.info(f"store_hunter: {result['removed']} dead stores removed")
    except Exception as e:
        logger.debug(f"store_hunter: discovery failed: {e}")


async def _run_scrapers(scrapers: list[Scraper]) -> list[tuple[str, list[RawSignal], Exception | None]]:
    """Run all scrapers in parallel — NO DB connection needed."""
    async def _run_one(s: Scraper) -> tuple[str, list[RawSignal], Exception | None]:
        try:
            rows = await s.fetch()
            return s.name, rows, None
        except Exception as e:  # noqa: BLE001
            return s.name, [], e

    tasks = [_run_one(s) for s in scrapers]
    return await asyncio.gather(*tasks)


def _build_product_rows(results: list[tuple[str, list[RawSignal], Exception | None]], run_ts: datetime) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """Build product rows + summary from scraper results. No DB I/O."""
    product_sets: dict[str, list[dict]] = {}
    summary: dict[str, int] = {}

    for name, rows, err in results:
        if err is not None:
            summary[name] = 0
            continue

        product_rows = []
        for sig in rows:
            payload = sig.payload or {}
            price: float | None = None
            currency = payload.get("currency") or "USD"

            if "variants" in payload:
                for v in payload.get("variants") or []:
                    raw = v.get("price")
                    if raw is not None:
                        try:
                            price = float(raw)
                            break
                        except (ValueError, TypeError):
                            continue
            elif "price" in payload and payload["price"] is not None:
                try:
                    price = float(payload["price"])
                except (ValueError, TypeError):
                    price = None

            store_base = payload.pop("_store_url", "")
            handle = payload.get("handle") or sig.external_id
            if store_base and handle:
                full_url = f"{store_base}/products/{handle}"
            else:
                full_url = payload.get("url") or ""

            product_rows.append({
                "source": sig.source,
                "external_id": sig.external_id,
                "captured_at": sig.captured_at,
                "title": payload.get("title"),
                "price": price,
                "currency": currency,
                "url": full_url,
                "payload": json.dumps(payload) if payload else None,
            })

        product_sets[name] = product_rows
        summary[name] = len(product_rows)

    return product_sets, summary


def _persist_results(storage: DuckDBStorage, results: list[tuple[str, list[RawSignal], Exception | None]], product_sets: dict[str, list[dict]], scrapers: list[Scraper], run_ts: datetime) -> dict[str, int]:
    """Persist all scraper results to DB — holds writer lock briefly."""
    summary: dict[str, int] = {}

    for name, _rows, err in results:
        if err is not None:
            _upsert_health(
                storage, name, HealthState.FAILING.value,
                run_ts, None, 0, 1.0, str(err)[:500],
            )
            summary[name] = 0
            continue

        product_rows = product_sets.get(name, [])
        written = storage.upsert("products", product_rows) if product_rows else 0

        _upsert_health(
            storage, name, HealthState.OK.value,
            run_ts, run_ts, written, 0.0, f"wrote {written} products",
        )
        summary[name] = written
        logger.info(f"ingest: {name} → {written} products")

    # Cleanup stale health entries for deactivated sources
    active_names = {s.name for s in scrapers}
    stale_rows = storage.query("SELECT source FROM health")
    stale_sources = [r["source"] for r in stale_rows if r["source"] not in active_names]
    if stale_sources:
        for src in stale_sources:
            storage.execute("DELETE FROM health WHERE source = ?", (src,))
        logger.info(f"ingest: removed {len(stale_sources)} stale health entry/ies: {stale_sources}")

    return summary


async def run_once(storage: DuckDBStorage | None = None, scrapers: list[Scraper] | None = None) -> dict:
    """Fetch from every scraper in parallel; persist RawSignals + health rows.

    Network scraping happens WITHOUT holding the DB writer lock, so the
    dashboard can always read fresh data. The lock is only held during
    the brief persist step at the end.

    Parameters
    ----------
    storage:
        Optional pre-opened writer. If None, a writer is opened internally
        for the brief persist step and closed immediately after.
    scrapers:
        Optional scraper list. If None, uses default_scrapers().

    Returns a dict {scraper_name: rows_written}.
    """
    # Auto-discover new Shopify stores before scraping
    await _run_store_discovery()

    scrapers = scrapers if scrapers is not None else default_scrapers()
    if not scrapers:
        logger.warning("ingest: no scrapers registered")
        return {}

    # Phase 1: Scrape all sources — NO DB LOCK held
    logger.info("ingest: scraping sources...")
    results = await _run_scrapers(scrapers)
    run_ts = datetime.now(UTC)

    # Phase 2: Build product rows from scraped data — NO DB LOCK held
    product_sets, summary = _build_product_rows(results, run_ts)

    # Phase 3: Persist to DB — holds writer lock briefly
    logger.info("ingest: persisting to DB...")
    if storage is not None:
        _persist_results(storage, results, product_sets, scrapers, run_ts)
    else:
        from trend_hunter.adapters.storage_duckdb import open_storage
        with open_storage() as s:
            _persist_results(s, results, product_sets, scrapers, run_ts)

    return summary
