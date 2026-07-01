"""Frozen dataclasses for all internal data shapes.

IO validation (Pydantic, email format, URL parsing) happens at the adapter
boundary, not here — keeping these types pure data with zero dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


# ── enums ────────────────────────────────────────────────────────────────────
class TrendStatus(str, Enum):
    RISING = "rising"
    DECLINING = "declining"
    STABLE = "stable"
    UNKNOWN = "unknown"


class HealthState(str, Enum):
    OK = "ok"
    STALE = "stale"
    FAILING = "failing"
    DEAD = "dead"
    UNKNOWN = "unknown"


# ── core domain ──────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class RawSignal:
    """One observation from one scraper; the unit of ingest."""
    source: str                # e.g. "shopify", "meta_ads", "reddit"
    external_id: str           # e.g. shop product id, Reddit post id
    captured_at: datetime
    payload: dict              # free-form, source-specific

    @property
    def key(self) -> tuple[str, str, datetime]:
        """Idempotency key — same source+id+ts = same row."""
        return (self.source, self.external_id, self.captured_at)


@dataclass(frozen=True, slots=True)
class Product:
    source: str
    external_id: str
    title: str
    price: float | None
    currency: str
    url: str
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class Lead:
    """B2B lead — GDPR-safe (business emails only)."""
    domain: str
    company_name: str
    contact_email: str | None
    contact_url: str
    source_url: str
    score: float
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class Forecast:
    sku: str
    horizon_days: int
    point_estimate: float
    lower_80: float
    upper_80: float
    confidence: float                # 0..1


@dataclass(frozen=True, slots=True)
class Money:
    """Arbitrage summary for one product."""
    sku: str
    retail_price: float
    supplier_cost: float
    shipping_cost: float
    cac_estimate: float
    margin_pct: float                # 0..1
    currency: str


@dataclass(frozen=True, slots=True)
class Health:
    """Per-source health snapshot."""
    source: str
    state: HealthState
    last_run: datetime | None
    last_ok: datetime | None
    rows_in: int                     # cumulative
    error_rate: float                # 0..1
    detail: str
