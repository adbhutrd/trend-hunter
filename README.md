# trend-hunter

> **Self-hosted Shopify trend-finder, B2B lead-generator, and arbitrage engine.**
> Free. Local. Private. Built for one operator.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Storage: DuckDB](https://img.shields.io/badge/storage-DuckDB-yellow.svg)](trend_hunter/adapters/storage_duckdb.py)
[![Dashboard: Streamlit](https://img.shields.io/badge/dashboard-Streamlit-ff4b4b.svg)](trend_hunter/ui/Home.py)
[![GDPR-safe](https://img.shields.io/badge/GDPR-B2B%20only-success.svg)](#privacy--gdpr)
[![Cost: \$0/mo](https://img.shields.io/badge/cost-%240-brightgreen.svg)]()
[![License: AGPL-3](https://img.shields.io/badge/License-AGPL--3-blue.svg)](LICENSE)

**One sentence:** trend-hunter is a closed-loop operator tool — it scrapes trending products, matches them against suppliers, drafts Shopify listings, **and** self-monitors its own success rate with a feedback engine that pushes degradation alerts to your phone.

---

## What it does

1. **Scrapes** public Shopify `/products.json` endpoints to track prices, inventory, and trend velocity.
2. **Classifies** every product as *rising / declining / stable* (least-squares slope over 14+ days).
3. **Matches** trending products against an arbitrage supplier catalog (CSV-driven; mock fallback for tests).
4. **Drafts and pushes** the top-N profitable rows to your Shopify dev store — dry-run by default, `--live` to actually POST.
5. **Validates** ad longevity in Meta Ad Library + TikTok (≥30 days running = secret profit signal).
6. **Self-monitors**: every CLI command is wrapped in a `record_run()` ledger. When any core command drops below a 50% success rate, **you get a Discord or Telegram alert.**

---

## Install

Python 3.12 or newer.

```bash
git clone <your-private-repo-url> trend-hunter
cd trend-hunter

# 1. virtualenv + dev deps
make init

# 2. (optional) configure alerts — interactive wizard
bash scripts/setup.sh

# 3. smoke-test
make run && make loop-report
```

That's the full bootstrap. No cloud account, no credit card, no Docker, no Kafka.

---

## Quick start

```bash
make scan            # ingest from all enabled sources
make run             # full daily cycle: scan → classify → aggregate → doctor
make money           # arbitrage + scaffold top-3 (dry-run)
make loop-report     # 6-section feedback summary
make alert           # push degradation notices if any
make dashboard       # Streamlit on http://localhost:8501
```

The first run will fetch sample data from the public Shopify demo stores already configured in [`sources.json`](sources.json); you can replace that file with your own store list at any time.

---

## Architecture

```
                       trend-hunter — closed-loop pipeline
                       ──────────────────────────────────

   sources.json              ┌──────────────────────────┐              data/suppliers.csv
   (Shopify demo             │   ingest/runner.py       │              (CSV catalogue of
    or your own)             │   asyncio.gather(        │              arbitrage suppliers)
        │                    │     all_scrapers)        │                    │
        ▼                    └────────────┬─────────────┘                    ▼
   ┌─────────┐  httpx + tenacity           │ write                  ┌──────────┐
   │ Scrape  │ ────────────────────────────▶│ ───────────────┐       │arbitrage │
   └─────────┘                              ▼                │       │  match   │
                                  ┌──────────────────────────────────┐ └────┬─────┘
                                  │       DuckDB   single file       │      │
                                  │                                  │      │
                                  │  raw:        products, leads     │      │
                                  │  aggregates: agg_*   (Parquet)   │      │
                                  │  ops:        run_history,        │      │
                                  │               calibrate,          │      │
                                  │               corrective_actions  │      │
                                  │  compliance: rop_audit           │      │
                                  │  + ON DISK flock + duplicate-key │      │
                                  │    idempotency                   │      │
                                  └────┬────┬──────┬──────┬──────┬────┘       │
                                       │    │      │      │      │            │
                              classify │    │      │      │      │            │
                              aggregate│    │      │      │      │            │
                                       ▼    ▼      ▼      ▼      ▼            │
                                  ┌─────────┐ ┌──────────┐ ┌──────────────┐    │
                                  │intel    │ │forecast  │ │   scaffold   │ ◀──┘
                                  │rising / │ │Baseline  │ │  draft → push │
                                  │declining│ │EWMA+80%  │ │  (--live)     │
                                  │stable   │ │CI        │ │     │        │
                                  └────┬────┘ └────┬─────┘ └──────┬──────┘
                                       │          │               │
                                       │          │               ▼
                                       │          │          TH_SHOPIFY_DOMAIN
                                       │          │          + TH_SHOPIFY_TOKEN
                                       │          │          → Shopify Admin API
                                       │          │
                                       ▼          ▼
                                  ┌──────────────────────────┐
                                  │  EVERY CLI command is    │
                                  │  wrapped in record_run() │ ─▶ run_history table
                                  └──────────────┬───────────┘
                                                 │ query
                                                 ▼
                                  ┌──────────────────────────┐
                                  │  loop-report (6 sections)│
                                  │   1. last run / command  │
                                  │   2. throughput + succ%  │
                                  │   3. counter totals      │
                                  │   4. forecast MAPE       │
                                  │   5. corrective actions  │
                                  │   6. dollars on track    │
                                  └──────────────┬───────────┘
                                                 │
                                                 ▼
                                  ┌──────────────────────────┐
                                  │   observe/               │
                                  │     alerts.py            │ ──┐
                                  │     calibrate.py         │   │ degrade?
                                  │     corrective_actions   │   │
                                  └──────────────┬───────────┘   │
                                                 │               │
                                                 ▼               │
                                  ┌──────────────────────────┐   │
                                  │         notify()         │ ◀─┘
                                  │   Discord webhook  ──▶   │
                                  │   OR Telegram bot   ──▶  │ ─▶ your phone
                                  └──────────────────────────┘
```

The bottom of the diagram is the **feedback loop**: every CLI command writes to `run_history`; the loop-report queries that ledger; alerts fire when any core command's success rate drops below 50%; corrective actions get suggested and tracked automatically.

---

## Command reference

Every command is exposed two ways: as a `python -m trend_hunter.cli` subcommand, and as a `make` target for ergonomic daily use.

### Daily operations

| Goal                                  | Command                                   |
|---------------------------------------|-------------------------------------------|
| Ingest from all sources               | `make scan` / `python -m trend_hunter.cli scan` |
| Full daily cycle                      | `make run` / `python -m trend_hunter.cli run` |
| Build dashboard roll-ups              | `make aggregate` / `python -m trend_hunter.cli aggregate` |
| Self-test (DB, schema, freshness)     | `make doctor` / `python -m trend_hunter.cli doctor` |
| Apply schema migrations               | `python -m trend_hunter.cli migrate` |
| Row counts per table                  | `python -m trend_hunter.cli stats` |

### Money layer

| Goal                                          | Command                                                        |
|-----------------------------------------------|----------------------------------------------------------------|
| Smoke-test the forecaster                     | `make forecast` / `python -m trend_hunter.cli forecast` |
| Match trending ↔ suppliers                    | `make arbitrage` / `python -m trend_hunter.cli arbitrage` |
| List ads ≥ 30 days running (validated profit) | `make ads` / `python -m trend_hunter.cli validate-ads` |
| Draft + push top-N (dry-run default)          | `make scaffold` / `python -m trend_hunter.cli scaffold --top 3` |
| Draft + push (push to Shopify)                | `python -m trend_hunter.cli scaffold --top 3 --live` |
| Arbitrage + scaffold in one call (chains alerts) | `make money` / `python -m trend_hunter.cli money-sweep --top 3` |

### Feedback loop (the closed back-half)

| Goal                                                | Command                                                                   |
|-----------------------------------------------------|---------------------------------------------------------------------------|
| 6-section feedback summary                          | `make loop-report` / `python -m trend_hunter.cli loop-report --days 7` |
| Forecast accuracy (MAPE/MAE)                        | `make calibrate` / `python -m trend_hunter.cli calibrate summary --days 7` |
| List pending calibrations awaiting actuals          | `python -m trend_hunter.cli calibrate list --days 30` |
| Fill in a realised price                            | `python -m trend_hunter.cli calibrate update <calibrate_id> <actual_price>` |
| Check success rates + push notifications            | `make alert` / `python -m trend_hunter.cli alert` |

### Live ops

| Goal                          | Command                                                                                       |
|-------------------------------|-----------------------------------------------------------------------------------------------|
| Start APScheduler (foreground) | `make schedule` / `python -m trend_hunter.cli schedule` |
| Launch Streamlit dashboard    | `make dashboard` / `python -m trend_hunter.cli dashboard` (default port `8501`) |
| GDPR Art. 17 erasure          | `python -m trend_hunter.cli forget <email>` (deletes lead rows + writes hashed audit row) |

`make help` lists every target with a one-line description.

---

## Configuration

All configuration is environment-driven (loaded via [`python-dotenv`](https://github.com/theskumar/python-dotenv) from a `.env` file at the project root on startup). Copy [`env.example`](.env.example) (or `bash scripts/setup.sh` to do it interactively) and edit:

| Env var                 | Default                       | Purpose                                                  |
|-------------------------|-------------------------------|----------------------------------------------------------|
| `TH_DB_PATH`            | `./data/trends.duckdb`        | DuckDB file location                                     |
| `TH_LOG_LEVEL`          | `INFO`                        | `DEBUG` / `INFO` / `WARNING` / `ERROR`                   |
| `TH_LOG_DIR`            | `./data/logs`                 | loguru JSONL rotation target                             |
| `TH_DASH_PORT`          | `8501`                        | Streamlit listener                                       |
| `TH_SCAN_INTERVAL_MINUTES` | `120`                      | APScheduler cadence (Phase 2)                            |
| `TH_AGGREGATE_HOUR`     | `3`                           | Daily aggregation time (Phase 2)                         |
| `TH_DISCORD_WEBHOOK`    | _(unset)_                     | Discord webhook URL — alerts auto-send here if set       |
| `TH_TELEGRAM_BOT_TOKEN` | _(unset)_                     | Telegram bot token — pair with `TH_TELEGRAM_CHAT_ID`     |
| `TH_TELEGRAM_CHAT_ID`   | _(unset)_                     | Telegram chat/DM/user id                                 |
| `TH_SHOPIFY_DOMAIN`     | _(unset)_                     | Required only when using `scaffold --live`               |
| `TH_SHOPIFY_TOKEN`      | _(unset)_                     | Required only when using `scaffold --live`               |

For Telegram specifically, the project ships a one-shot wiring helper:

```bash
bash scripts/wire_telegram.sh <BOT_TOKEN>
```

That validates the token via `getMe`, extracts `chat_id` from `getUpdates` (filtering to private non-bot users, preferring an existing `.env` value for idempotency), upserts both vars into `.env`, sets `.env` to mode 600, and dispatches a real test alert.

`.gitignore` blocks `.env` so credentials never leak.

---

## How the feedback loop works

1. **Every CLI subcommand** opens storage via `record_run("command", storage=storage)` from [`trend_hunter/observe/run_ledger.py`](trend_hunter/observe/run_ledger.py). On exit, it records `{started_at, finished_at, status, duration_ms, error_type, counters: {...}}` into the `run_history` table, whether the command succeeded, failed with an `Exception`, or was cancelled.
2. **`loop-report`** reads the last `N` days of `run_history`, partitions it into 6 sections (last run / throughput / counters / forecast MAPE / corrective actions / dollars-on-track), and computes deltas vs the prior window so drift shows up at a glance.
3. **`calibrate`** records every price forecast into `calibrate`; later you fill in the realised price with `calibrate update`, and MAPE / MAE roll up over time.
4. **`corrective_actions`** is the auto-playbook: when degradation is detected, suggested fixes get written here as `pending`; a future you (or a CI job) marks them `resolved`/`failed`/`skipped`.
5. **`alerts`** checks every core command's 1-day success rate; if any command falls into the `0 < rate < 0.5` band, `notify()` pushes a message to **both** Discord (if `TH_DISCORD_WEBHOOK` is set) and Telegram (if both token and chat id are set).
6. **`money-sweep`** finishes by chain-invoking `loop-report` and `alert` so every profitable run produces a fresh feedback snapshot.

You can read the ledger directly with `python -c` (DuckDB is just a single file, nothing fancy):

```bash
.venv/bin/python - <<'PY'
import duckdb
con = duckdb.connect("data/trends.duckdb", read_only=True)
for row in con.execute(
    "SELECT command, status, started_at, duration_ms FROM run_history "
    "ORDER BY started_at DESC LIMIT 10"
).fetchall():
    print(row)
PY
```

---

## Privacy + GDPR

- **B2B contacts only.** Never scrape personal emails or social bios. Legitimate-interest basis (GDPR Art. 6.1.f).
- **First-paragraph disclosure** in every cold email (where you found them).
- **Mandatory one-click unsubscribe.**
- **Record-of-Processing CSV** auto-built per extraction (Art. 30).
- **Right-to-erasure** is one command: `python -m trend_hunter.cli forget <email>` removes the lead rows and writes a SHA-256 retention marker to `rop_audit`.

---

## Reliability principles

Every module is designed against five rules:

| Principle      | Where it shows up                                                                                              |
|----------------|----------------------------------------------------------------------------------------------------------------|
| Idempotent      | Composite-primary-key upserts in `adapters/storage_duckdb.py`; `record_run()` accepts a `dedupe_key` parameter. |
| Observable      | Every command wraps storage in `record_run()`; results stay in `run_history`; loop-report surfaces them.        |
| Cancellable     | `KeyboardInterrupt` ⇒ row tagged `status='err'`, error_type=`KeyboardInterrupt`.                               |
| Bounded         | Scraper uses bounded `asyncio.Semaphore`; tenacity retry budget is finite; jobs respect lock timeouts.        |
| Self-testing    | `cli doctor` returns rc 0/1/2 ↔ green/yellow/red; pytest + ruff both clean in CI.                              |

Concurrent-writer safety: the DuckDB file uses an `O_CREAT|O_RDWR, 0o600` flock with an **exponential-with-jitter** retry (50 → 100 → 200 → 400 ms + 0–10 ms jitter, up to N attempts). Every `with record_run(...)` block propagates the storage handle so the inner code never re-acquires the lock.

---

## Development

```bash
make test          # pytest (69 tests, async mode auto)
make lint          # ruff + mypy (lenient — Phase 1 noise tolerated)
make clean         # drop caches + .egg-info + __pycache__
```

### Project layout

```
trend-hunter/
├── cli.py                        # argparse entrypoint (16 subcommands)
├── trend_hunter/
│   ├── core/                     # protocol seams, frozen types, config, logging
│   ├── adapters/                 # concrete impls of the seams (DuckDB, httpx, Shopify)
│   ├── ingest/                   # orchestrates scrapers in parallel
│   ├── intelligence/             # classifier + nightly aggregator
│   ├── money/                    # arbitrage + scaffolder + money-sweep
│   ├── leads/                    # GDPR-safe B2B extractor
│   ├── flows/                    # APScheduler wiring (Phase 2)
│   ├── observe/                  # ← the feedback loop (alerts, calibrate,
│   │                                corrective_actions, run_ledger, loop_report)
│   ├── ui/                       # Streamlit multipage dashboard
│   └── scripts/                  # doctor + aggregate
├── tests/                        # pytest, fast + isolated to tmp_path
├── scripts/
│   ├── setup.sh                  # interactive alert wizard (Discord + Telegram)
│   └── wire_telegram.sh          # one-shot Telegram bot wiring (validates token,
│                                  extracts chat_id, locks .env to 600, tests dispatch)
├── data/
│   ├── trends.duckdb             # single-file local DB (gitignored)
│   ├── suppliers.csv             # arbitrage catalog (20+ rows of mock suppliers)
│   └── logs/                     # loguru JSONL rotation target
├── Makefile                      # ergonomics wrapper for the CLI
├── sources.json                  # Shopify store list to scrape
├── pyproject.toml                # PEP 621 metadata
└── .env.example                  # template for `.env`
```

### Testing style

- All tests use the `storage` fixture in [`tests/conftest.py`](tests/conftest.py) which points at a per-test isolated DuckDB under `tmp_path` — no shared-state contention.
- `record_run` reads use `try/except` wrappers rather than `pytest.raises` so that *intentional* `RuntimeError`s from inside the SUT don't leak the wrapper itself.
- Async tests run in `asyncio_mode = "auto"` per `pyproject.toml`.
- Mocks live next to the contracts (`MockSupplier`, `MockMailer`, etc.) so swapping them out is a one-line change.

---

## License

[AGPL-3](LICENSE). The AGPL was chosen so that any third-party SaaS fork must publish its source. **Personal use, modification, and self-hosting are unrestricted.** No warranty — see `LICENSE` for the full text.
