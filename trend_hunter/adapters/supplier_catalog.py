"""Supplier Protocol adapters for the arbitrage pipeline.

We deliberately don't reach AliExpress / CJ Dropshipping directly in Phase 3
(both enforce strict rate-limits and bounce cloud IPs). Instead we read a
local CSV/TSV catalog that you curate once, then refresh weekly.

Schema for `data/suppliers.csv` (header required, comma-separated):

    title_pattern,sku,supplier,url,cost_usd,ships_from,eta_days

Example:
    alloy desk lamp,*lum-001,Lumify,https://lumify.example.com/p/001,8.40,CN,12
"""
from __future__ import annotations

import csv
import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Supplier:
    title_pattern: str
    sku: str
    supplier: str
    url: str
    cost_usd: float
    ships_from: str
    eta_days: int


class SupplierCatalog(Protocol):
    """Find suppliers matching a free-text product title."""
    def find(self, query: str, limit: int = 5) -> list[Supplier]: ...


# ── CSV-backed adapter ────────────────────────────────────────────────────────
class CsvCatalogSupplier:
    """Read suppliers from a CSV catalog. Wildcards (`*`) supported in patterns."""

    def __init__(self, csv_path: Path = Path("./data/suppliers.csv")) -> None:
        self.csv_path = csv_path
        self._items: list[Supplier] = []
        self._load()

    def _load(self) -> None:
        self._items = []
        if not self.csv_path.exists():
            return
        with self.csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    self._items.append(
                        Supplier(
                            title_pattern=row["title_pattern"].strip(),
                            sku=row["sku"].strip(),
                            supplier=row["supplier"].strip(),
                            url=row["url"].strip(),
                            cost_usd=float(row["cost_usd"]),
                            ships_from=row["ships_from"].strip(),
                            eta_days=int(row["eta_days"]),
                        )
                    )
                except (KeyError, ValueError):
                    # skip malformed row without breaking the whole load
                    continue

    def reload(self) -> None:
        self._load()

    def find(self, query: str, limit: int = 5) -> list[Supplier]:
        q = (query or "").lower()
        matches: list[Supplier] = []
        for s in self._items:
            if not s.title_pattern:
                continue
            pat = s.title_pattern.lower()
            if "*" in pat:
                if fnmatch.fnmatch(q, pat):
                    matches.append(s)
            elif pat in q:
                matches.append(s)
        return matches[: max(0, limit)]


# ── In-memory adapter for tests / paper-mode ──────────────────────────────────
class MockSupplier:
    """Hard-coded catalog with two representative products."""

    def __init__(self) -> None:
        self._items: list[Supplier] = [
            Supplier(
                title_pattern="*desk lamp*",
                sku="lum-mock-001",
                supplier="Lumify",
                url="https://lumify.example.com/p/001",
                cost_usd=8.40,
                ships_from="CN",
                eta_days=12,
            ),
            Supplier(
                title_pattern="*resistance band*",
                sku="fit-mock-007",
                supplier="FitFlex",
                url="https://fitflex.example.com/p/007",
                cost_usd=3.20,
                ships_from="US",
                eta_days=3,
            ),
        ]

    def find(self, query: str, limit: int = 5) -> list[Supplier]:
        q = (query or "").lower()
        out = []
        for s in self._items:
            if fnmatch.fnmatch(q, s.title_pattern.lower()):
                out.append(s)
        return out[: max(0, limit)]
