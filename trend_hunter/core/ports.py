"""Protocol seams.

These are the only types you may need to redefine if you later swap
DuckDB → ClickHouse, httpx → Playwright, Gmail SMTP → SES, or
Streamlit → Reflex. Adapters implement these protocols; everything
else (flows, intelligence, UI) talks only to the protocols.

Run `make test` and `tests/test_storage_protocol.py` will catch
any contract drift between an adapter and its protocol.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from .types import (
    Forecast,
    Health,
    Lead,
    Money,
    RawSignal,
)


class Scraper(Protocol):
    """One source of trend/lead signals.

    Implementations MUST:
    * constrain concurrency (token bucket per domain)
    * back off on 429/503 with jitter
    * emit RawSignals that round-trip through Storage.upsert idempotently
    * raise on plugin-fatal errors; never swallow
    """
    name: str

    async def fetch(self) -> list[RawSignal]: ...
    async def health(self) -> Health: ...


class Storage(Protocol):
    """All stateful data lives here.

    Implementations MUST:
    * be idempotent for upsert (same key → no-op or update)
    * treat `query` as a read-only, snapshot view
    * be safe under concurrent multi-process readers (writer is exclusive)
    """
    def upsert(self, table: str, rows: list[dict]) -> int: ...
    def query(self, sql: str, params: tuple = ()) -> list[dict]: ...
    def execute(self, sql: str, params: tuple = ()) -> None: ...
    async def health(self) -> list[Health]: ...


class Aggregator(Protocol):
    """Build pre-aggregated Parquet rollups from raw rows.

    Called by `make aggregate` nightly. Outputs files under
    data/aggregates/*.parquet consumed by the dashboard.
    """
    def run(self) -> list[Path]: ...


class Forecaster(Protocol):
    """Given price/volume history, return a Forecast with confidence interval."""
    def fit_predict(
        self, history: list[tuple[datetime, float]], horizon_days: int,
    ) -> Forecast: ...


class Mailer(Protocol):
    """Send transactional + bulk email. Dry-run by default."""
    async def send(self, lead: Lead, template: str, dry_run: bool = True) -> dict: ...


class MoneyCalculator(Protocol):
    """Compute margin for one (retail, supplier) pair. Pure function."""
    def margin(self, retail: float, supplier: float, shipping: float, cac: float) -> Money: ...
