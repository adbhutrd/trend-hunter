"""Ingest runner — orchestrate all enabled scrapers in parallel and persist results.

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


# ── registration ─────────────────────────────────────────────────────────────
def default_scrapers() -> list[Scraper]:
    """Build the default scraper set. Extend here as new adapters land."""
    from trend_hunter.adapters.scraper_httpx_shopify import from_sources_json

    return [from_sources_json(Path("./sources.json"))]


async def run_once(storage: DuckDBStorage, scrapers: list[Scraper] | None = None) -> dict:
    """Fetch from every scraper in parallel; persist RawSignals + health rows.

    Returns a dict {scraper_name: rows_written}.
    """
    scrapers = scrapers if scrapers is not None else default_scrapers()
    if not scrapers:
        logger.warning("ingest: no scrapers registered")
        return {}

    async def _run_one(s: Scraper) -> tuple[str, list[RawSignal], Exception | None]:
        try:
            rows = await s.fetch()
            return s.name, rows, None
        except Exception as e:                                        # noqa: BLE001
            return s.name, [], e

    tasks = [_run_one(s) for s in scrapers]
    results = await asyncio.gather(*tasks)

    summary: dict[str, int] = {}
    run_ts = datetime.now(UTC)

    for name, rows, err in results:
        if err is not None:
            logger.error(f"ingest: {name} failed: {err}")
            storage.upsert(
                "health",
                [{
                    "source": name,
                    "state": HealthState.FAILING.value,
                    "last_run": run_ts,
                    "last_ok": None,
                    "rows_in": 0,
                    "error_rate": 1.0,
                    "detail": str(err)[:500],
                }],
            )
            summary[name] = 0
            continue

        # Persist raw signals as Product snapshots (extractors run later).
        product_rows = []
        for sig in rows:
            payload = sig.payload or {}
            variants = payload.get("variants") or []
            price = None
            currency = payload.get("currency") or "USD"
            for v in variants:
                if isinstance(v.get("price"), (int, float)):
                    price = float(v["price"])
                    break
            product_rows.append({
                "source": sig.source,
                "external_id": sig.external_id,
                "captured_at": sig.captured_at,
                "title": payload.get("title"),
                "price": price,
                "currency": currency,
                "url": f"{payload.get('url') or ''}/products/{payload.get('handle') or sig.external_id}",
                "payload": json.dumps(payload) if payload else None,
            })

        written = storage.upsert("products", product_rows) if product_rows else 0

        storage.upsert(
            "health",
            [{
                "source": name,
                "state": HealthState.OK.value,
                "last_run": run_ts,
                "last_ok": run_ts,
                "rows_in": written,
                "error_rate": 0.0,
                "detail": f"wrote {written} products",
            }],
        )
        summary[name] = written
        logger.info(f"ingest: {name} → {written} products")

    return summary
