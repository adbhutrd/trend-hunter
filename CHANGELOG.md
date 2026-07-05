# Changelog

All notable changes to **trend-hunter** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Phase 3 — Money-making layer** (this commit is the headline)
  - `adapters/forecaster_baseline.py` — EWMA-based forecaster with 80% CI
  - `adapters/supplier_catalog.py` — Supplier Protocol + CSV-backed `CsvCatalogSupplier` + in-memory `MockSupplier` for tests
  - `adapters/ads_validator.py` — read `data/ads.json`; flag ads ≥ 30 days running as validated profit
  - `adapters/shopify_push.py` — Shopify Admin API push via httpx; dry-run by default unless `TH_SHOPIFY_DOMAIN` + `TH_SHOPIFY_TOKEN` env vars are set
  - `money/arbitrage.py` — orchestrates trending products ↔ suppliers; persists profitable matches to `money` table
  - `money/scaffold_pipeline.py` — converts top-margin `MoneyRow` into Shopify `ProductDraft` + push
  - `flows/money_sweep.py` — full money cycle in one call
  - `flows/daily.py` — APScheduler BackgroundScheduler; tier-only, no auto-start
- 5 new CLI subcommands: `forecast`, `arbitrage`, `validate-ads`, `scaffold --top 3 [--live]`, `money-sweep`, `schedule`
- 4 new Makefile targets: `forecast`, `arbitrage`, `ads`, `scaffold`, `money`, `money-cycle`
- 4 new test files: `test_forecaster.py`, `test_arbitrage.py`, `test_scaffold.py`, `test_ads_validator.py`
- **Forecast back-test leg of the loop** (closes the gap flagged in `docs/LOOP_ENGINEERING.md`)
  - `observe/calibrate.auto_resolve_calibrations(storage, days_lookback=30, dry_run=False)` — finds overdue pending calibrations and resolves them against the `products` table by matching `calibrate.sku ↔ products.external_id` (most recent post-forecast price). Idempotent (re-running on resolved rows is a no-op); dry-run mode reports counts without writing.
  - `intelligence/aggregator.aggregate()` now calls `auto_resolve_calibrations()` after building roll-ups, so every nightly `make run` quietly settles anything that's become due.
  - New CLI subcommand: `calibrate backfill [--days N] [--dry]` — one-shot wrapper for explicit rescues.
  - New Makefile target `calibrate-backfill` (defaults to days=30).
  - New `COUNTER_KEYS` entry `calibrations_resolved` — surfaces in `loop-report` automatically.
  - 5 new test cases in `tests/test_calibrate.py`: overdue-pickup, idempotency, dry-run non-write, missing-match no-crash, pre-horizon skip.

### Changed

- `Makefile` — added `money`, `money-cycle`, `schedule`, `forecast`, `arbitrage`, `ads`, `scaffold` targets
- `cli.py` — added 6 subcommands; refactored `_cmd_*` helpers via `_open_reader`/`_open_writer`

### Fixed

- **`rop_audit` migration**: renamed `at` → `recorded_at` because DuckDB reserves `at` for time-travel queries
- **`storage_duckdb.upsert`**: now `df.reindex(columns=schema)` so unknown dict keys cannot crash the schema; column-mismatch is impossible
- **`_cmd_forget`**: switched from `storage.upsert` (dict→reindex→NOT NULL crash path) to raw `storage.execute INSERT … ON CONFLICT DO NOTHING`; idempotent across repeated calls
- **flock**: tightened to `os.open(O_CREAT|O_RDWR, 0o600)` to eliminate the trivial concurrent-creator race
- **migrations**: wrapped each in `BEGIN/COMMIT/ROLLBACK` so a partial DDL failure can't wedge the schema
- **tests/test_smoke.py**: replaced the bugged relative `cwd="trend-hunter"` with absolute `PROJECT_ROOT` computed from `Path(__file__).parent.parent`

## [0.1.0] — Phase 1 — Pipeline Foundation

### Added

- Greenfield Git repo at `/home/anonwiz/trend-hunter` (private)
- `core/types.py` — frozen dataclasses (`Product`, `Lead`, `RawSignal`, `Forecast`, `Money`, `Health`)
- `core/ports.py` — Protocol seams (`Scraper`, `Storage`, `Aggregator`, `Forecaster`, `Mailer`, `MoneyCalculator`)
- `core/config.py` — Pydantic v2 settings with `TH_` env-var prefix
- `core/logging.py` — loguru with structured JSONL rotation
- `adapters/storage_duckdb.py` — DuckDBStorage with composite-PK idempotency + column cache + on-disk flock
- `adapters/scraper_httpx_shopify.py` — Shopify `/products.json` scraper with tenacity retry/jitter + bounded concurrency
- `adapters/mailer_stub.py` — Phase-1 dry-run mailer (Phase 2 will swap to Gmail SMTP via `aiosmtplib`)
- `ingest/runner.py` — orchestrates scrapers in parallel; per-source heartbeat
- `intelligence/classifier.py` — least-squares slope → rising/declining/stable
- `intelligence/aggregator.py` — nightly DuckDB rollups (`agg_product_status`, `agg_source_health`, `agg_saturation`)
- `scripts/doctor.py` — reliability self-test returned exit 0 on green / 1 on yellow / 2 on red
- `ui/Home.py` — landing page with health KPIs
- `ui/pages/1_🏠_health.py`, `2_📈_trends.py`, `8_⚙️_settings.py` — Streamlit multipage
- 5 test files: `test_storage_protocol.py`, `test_classifier.py`, `test_smoke.py`, conftest
- `pyproject.toml` (PEP 621, AGPL-3, Python 3.12+), `pyproject.toml` deps all install cleanly, `.env.example`, `sources.json`, `LICENSE`
