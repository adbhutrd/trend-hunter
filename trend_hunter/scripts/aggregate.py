"""Standalone aggregate runner — same as `make aggregate`."""

from __future__ import annotations

from trend_hunter.adapters.storage_duckdb import open_storage
from trend_hunter.core.config import get_settings
from trend_hunter.intelligence.aggregator import aggregate, classify_all


def main() -> None:
    get_settings()
    with open_storage(read_only=False) as storage:
        out = aggregate(storage)
        n = classify_all(storage)
    print(f"  ran_at={out['ran_at']}")
    print(f"  products={out['products']}")
    print(f"  sources={out['sources']}")
    print(f"  classified={n}")


if __name__ == "__main__":
    main()
