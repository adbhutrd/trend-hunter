# trend-hunter

> **Self-hosted Shopify trend-finder, B2B lead-generator, and arbitrage engine.**
> Free. Local. Private. Built for one operator.

[![Repo: private](https://img.shields.io/badge/repo-private-lightgrey.svg?logo=github)]()
[![License: AGPL-3](https://img.shields.io/badge/License-AGPL--3-blue.svg)]()
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)]()
[![Storage: DuckDB](https://img.shields.io/badge/storage-DuckDB-yellow.svg)]()
[![Dashboard: Streamlit](https://img.shields.io/badge/dashboard-Streamlit-ff4b4b.svg)]()
[![GDPR-safe](https://img.shields.io/badge/GDPR-B2B%20only-success.svg)]()
[![Hosting: local](https://img.shields.io/badge/host-100%25%20local-lightgrey.svg)]()
[![Cost: $0/mo](https://img.shields.io/badge/cost-%240-brightgreen.svg)]()

This is a **greenfield personal project**. It is not derived from, copied from, or merged with any other tool. Every line of code in this repo was written for this purpose.

## What it does

1. Scrapes public Shopify `/products.json` to track prices, inventory and trend velocity.
2. Pulls Google Trends, Reddit, Meta Ad Library, TikTok signals.
3. Fuses them into a single Trend Score per product (rising / declining / stable).
4. Extracts B2B leads (about-page emails, IG handles, LinkedIn slugs) — GDPR-safe.
5. Matches trending products against AliExpress / CJ Dropshipping suppliers for arbitrage.
6. Validates winners via Meta Ad Library + TikTok Spark Ads (>30 days running = secret profit signal).
7. Pushes validated products to your Shopify dev store via the API.
8. Sends cold emails via Gmail SMTP with mandatory one-click unsubscribe.
9. Runs entirely on **your laptop**. **No cloud. No SaaS. No card.**

## Philosophy

- **Local-first.** Your laptop is the data center. Free cloud tiers are traps.
- **Reliable over clever.** Idempotent + observable + self-testing beats fancy.
- **Snappy by default.** Polars + DuckDB + cached Streamlit fragments. Never lag.
- **Future-seamed.** Protocol classes in `core/ports.py` so dashboards, storage, scrapers, mailers can each be swapped without rewriting the rest.
- **GDPR-safe.** B2B only. Record-of-Processing CSV built automatically. One-click unsubscribe baked in.

## Quick start

```bash
git clone <your-private-repo-url> trend-hunter
cd trend-hunter
make init              # creates .venv, installs deps, wipes + prepares data/
make run               # ingest + classify + aggregate + doctor, single pass
make dashboard         # Streamlit on http://localhost:8501
```

That's it. No cloud account, no card, no docker-compose, no Kubernetes, no broker.

## Commands

```bash
make run               # full cycle (scan → classify → aggregate → doctor)
make scan              # ingest only
make doctor            # self-test (DB integrity, free disk, freshness)
make aggregate         # build Parquet rollups from raw rows
make backup            # encrypted DuckDB snapshot → external drive
make dashboard         # start the Streamlit dashboard
make test              # pytest
make lint              # ruff + mypy
```

## Architecture

```
trend_hunter/
├── core/              # Protocol seams, frozen types, config, logging
│   ├── ports.py       # Scraper / Storage / Forecaster / Mailer / Dashboard
│   ├── types.py       # Product, Lead, RawSignal, Forecast, Money, ...
│   └── config.py      # Pydantic settings (env-only)
├── adapters/          # concrete impls of the seams
│   ├── storage_duckdb.py
│   ├── scraper_httpx_shopify.py
│   └── mailer_stub.py
├── ingest/            # orchestrates all scrapers in parallel
├── intelligence/      # classifier (rising/declining/stable) + aggregator
├── leads/             # GDPR-safe B2B extractor (Phase 2)
├── money/             # arbitrage + ad-validator + scaffolder (Phase 3)
├── flows/             # APScheduler wiring (Phase 2)
├── ui/                # Streamlit multipage (Health, Trends, Leads, ...)
├── scripts/           # doctor, aggregate, backup
└── cli.py             # argparse entrypoint
```

## Why these choices

| Need | Picked | Why not the alternative |
|---|---|---|
| Storage | **DuckDB** (single file) | Postgres unnecessary at personal scale; adds ops |
| Transform | **pandas** for MVP / **Polars** when needed | Polars is faster but bigger dep; can swap via `Storage` seam |
| Scraper | **httpx async + tenacity** | Playwright is overkill for `/products.json` |
| Forecast | **ruptures** change-point + **Prophet** (gated ≥90 d) | Prophet over-smooths bursts; ruptures surfaces virality |
| Config | **Pydantic v2** | Hand-rolled dicts are bug magnets |
| Logging | **loguru → JSONL** | stdlib logging is fine but verbose |
| Scheduling | **APScheduler** | Prefect/Dagster are overkill for one operator |
| Mail | **aiosmtplib → Gmail SMTP** | Listmonk when you cross 500 emails/day |
| Dashboard | **Streamlit 1.36 multipage** | Dash/Reflex when you ship it to anyone but yourself |
| Validation tests | **pytest** | The widest toolchain support |
| Backup | **restic → external USB** | B2 is fine too, but local is cheaper |

## Privacy + GDPR

- B2B contacts only. Never scrape personal emails or social bios.
- Legitimate-interest basis (Art. 6.1.f GDPR).
- First-paragraph disclosure in every cold email (where you found them).
- Mandatory one-click unsubscribe (Listmonk handles, but Gmail SMTP works for low volume).
- Record-of-Processing CSV auto-built per extraction (Art. 30).
- Right-to-erasure: `python -m trend_hunter.cli forget <email>` removes lead + audit row (hashed retention marker).

## Reliability

Every module is designed against five principles: **idempotent**, **observable**, **cancellable**, **bounded**, **self-testing**. The full pipeline degrades gracefully: if `/products.json` returns 429, that source is suspended for 1 h and shows a red badge on the Health page; nothing else breaks.

## What's NOT here yet

- Prefect with SQLite backend (Phase 2; only when APScheduler feels light)
- Drift detection on raw vs aggregated (Phase 2)
- Adversarial testing of scrapers (Phase 3)
- TUI fallback for SSH-only operators (Phase 3)

## License

AGPL-3 — see [LICENSE](LICENSE).
The AGPL was chosen so that any third-party SaaS fork must publish its source. Personal use, modification, and self-hosting are unrestricted.
