# Loop Engineering in `trend-hunter`

This document explains how the project implements the four-loop
**sense → decide → act → measure** feedback pattern. If you're new
to the codebase, read [`README.md`](../README.md) first.

## Why feedback loops?

A long-lived autonomous system only stays useful if its outcomes
**close back to the operator**. Without explicit feedback, every
silent failure goes unnoticed: a Shopify killswitch trip, a
classifier stuck on "unknown" for a week, an aggregator quietly
out-of-date. Loop engineering makes the loop **visible, idempotent,
and self-correcting**.

## The four legs

```
            ┌──── money table writes ────┐
            │                            │
            ▼                            │
   scan ──► classify ──► arbitrage ──► scaffold ──► measure
     │         │           │              │
     └─────────┴───────────┴──────────────┘
                    │
                    └─► every leg writes one row to run_history
```

| Leg           | Owner file                             | What we sense |
| ------------- | -------------------------------------- | ------------- |
| **sense**     | `trend_hunter/ingest/runner.py`         | Live Shopify catalogs via `httpx` + `tenacity` retry/jitter |
| **decide**    | `trend_hunter/intelligence/classifier.py` | Least-squares slope on 7d prices; rising ≥ +2 %/day |
| **act**       | `trend_hunter/money/{arbitrage,scaffold_pipeline}.py` | Match trending × supplier catalog → margin → draft |
| **measure**   | `trend_hunter/observe/{run_ledger,loop_report}.py` | Side-table writes + dashboard counters |

## The spine: `run_history`

`run_history` (migration #2) is the *single SQL source of truth* the
system uses to know whether **the loop itself** is healthy:

| Column        | Purpose                                                  |
| ------------- | -------------------------------------------------------- |
| `run_id`      | UUIDv7-ish; sort = chronological                         |
| `command`     | The CLI verb (`scan`, `money-sweep`, `forget`, …)        |
| `started_at`  | Wall-clock at insert                                     |
| `finished_at` | When the work committed (or rolled back)                 |
| `duration_ms` | End-to-end cost; cheap QoS signal                        |
| `status`      | `ok` / `err` / `pending`                                 |
| `host`        | Hostname + pid so multi-machine installs stay separable  |
| `counters`    | JSON of `{"products_in": 30, "money_rows": 4, …}`        |
| `error_*`     | Populated only on failure paths                          |
| `dedupe_key`  | Optional; collapses retries into one row                 |

How to read it:

```sql
-- success rate per command (7 days)
SELECT command,
       count(*) FILTER (WHERE status='ok') * 1.0 / count(*)
FROM run_history
WHERE started_at >= now() - INTERVAL '7 days'
GROUP BY command
```

## How loops self-correct

Three concrete self-correction channels are wired today:

1. **Idempotent escalation** — every CLI verb is wrapped in
   `record_run(...)` with a `dedupe_key` for retried kinds
   (`forget <email>`). A retried `forget` reuses the same `run_id`,
   so retries collapse to one row and the `forget` count does not
   double.
2. **Doctor + loop-report** — `make doctor` reads
   `health.*` + `run_history`; `make loop-report` reads just
   `run_history` for a 7-day throughput trend with prior-period
   delta so drift is visible at a glance.
3. **Money page feedback** — `ui/pages/6_💰_money.py` pulls the
   last run per money-leg from `run_history` and renders it as
   KPI cards ("money-sweep 11m ago ✅"). If a leg is stale, the
   dashboard surfaces it without anyone needing to open a log.

## Loop-engineering checklist (when adding a new leg)

When you add a CLI subcommand, a scraper, or a money emit, follow
this checklist:

- [ ] The pure action lives in `trend_hunter/{adapters,intelligence,money}/...`
- [ ] The CLI verb wraps the action in `with record_run(...) as rec:`
- [ ] The body sets `rec.set_counters({...})` with the counter names
      the loop-report uses (see `loop_report.COUNTER_KEYS`)
- [ ] If the verb is user-retryable (e.g. `forget`), it passes a
      `dedupe_key` so retries don't double-row
- [ ] If the verb changes schema, it adds a new (`version`, SQL) tuple
      to `_MIGRATIONS`
- [ ] The Money page or dashboard surfaces the new counter via
      `_money_rows()` / `_last_runs()`

## What we don't (yet) measure honestly

- **Calibration of "rising" labels** — classifier hasn't logged
  whether its predictions later held. Tracked as a follow-up.
- **Ad spend attribution** — `data/ads.json` is a curated catalog;
  no real Meta API spend signal yet.

### ✅ Forecast back-test (closed)

`observe/calibrate.auto_resolve_calibrations(storage, days_lookback=30,
dry_run=False)` runs at the end of every `aggregate()`. It picks up
every pending `calibrate` row whose horizon has elapsed, joins
`calibrate.sku ↔ products.external_id`, and writes the most recent
post-forecast price via the existing `update_actual()` helper. The
resolved rows then feed `calibration_summary()` so `loop-report`
surfaces MAPE/MAE automatically. `make calibrate-backfill` is the
explicit one-shot escape hatch (with `--dry` for safe preview).
