# 🛍️ Shopping Search — Future Idea (NOT YET BUILT)

> **Status:** Designed in conversation, **not implemented**. When you come back to
> this, read "User pain" first, then "Architecture", then "File-level plan", then
> the "Risks / gotchas" section before touching anything.

---

## User pain

The original project, **trend-hunter**, is operator-facing: it scrapes Shopify
stores to find trending products to *re-sell* (B2B arbitrage). The user
expressed a separate, complementary wish:

> "I want to buy a phone. I don't want to go to all the sites to check price.
> Just compare a list down for me. That's the solution."

So this idea is: add a *consumer-facing* price-comparison surface on top of
the existing trend-hunter local database + dashboard.

---

## What this idea proposes

Two modes, both live alongside the existing trend-hunter dashboard:

1. **Single product** — type a keyword like `iphone 15` → table of stores
   ranked cheapest-first with prices + links.
2. **Cart** — paste a shopping list → per-store subtotal table + a single
   "best store" verdict for the whole list.

Live also as a terminal command:

```
python -m trend_hunter.cli search "iphone 15" [--json]
```

Powered by a new DuckDB cache table so repeat searches are instant, and a
"best store" pure function so the math is testable in isolation.

---

## Architecture (free only, no credit card)

| Choice        | What                                                                                             |
|---------------|--------------------------------------------------------------------------------------------------|
| Primary API   | **Brave Search API** — free ~$5 credit/month on signup (~1,000 queries), no card. Key in `.env` as `TH_BRAVE_API_KEY`. |
| Backup API    | **`duckduckgo-search`** PyPI package — no key, no signup, slower on purpose (2-10s between calls) so we don't get blocked. |
| Cache         | New DuckDB table `search_results` (migration #5) joined to the existing `trends.duckdb` file. |
| Parsing       | Pure regex on web snippets — extract `(store, price_usd, url)`. Guarded by sane price range $1-$9999 to dodge "Save $50 on $499" misfires. |
| Currencies    | USD-only for v0; non-USD prices dropped silently.                                                |
| UI            | New Streamlit page (`trend_hunter/ui/pages/2_🛍️_Search.py`) inside the existing dashboard.        |

Both APIs follow the existing `ShopifyScraper` pattern in
`adapters/scraper_httpx_shopify.py` (async httpx, asyncio.Semaphore,
tenacity retry-with-jitter).

---

## File-level plan (matches existing project conventions)

| Edit / New | Path                                          | Purpose                                                                        |
|------------|-----------------------------------------------|--------------------------------------------------------------------------------|
| Edit       | `trend_hunter/core/config.py`                 | Add `brave_api_key: str \| None` setting (env `TH_BRAVE_API_KEY`).              |
| Edit       | `trend_hunter/core/types.py`                  | Add `PriceOffer` + `CartResult` frozen dataclasses.                            |
| Edit       | `trend_hunter/core/ports.py`                  | Add `ProductSearcher` Protocol seam (mirrors `Scraper`).                       |
| Edit       | `trend_hunter/adapters/storage_duckdb.py`     | Add migration #5 (`search_results` table, PK on `(query, store_name, url)`).   |
| New        | `trend_hunter/adapters/product_search.py`     | `BraveSearcher` + `DDGSearcher` + cache helpers + `find_cheapest_per_store`.   |
| New        | `trend_hunter/ui/pages/2_🛍️_Search.py`       | Streamlit page (input box, results table, cart verdict).                       |
| Edit       | `trend_hunter/cli.py`                         | New subcommand `search "<query>"` wrapped in `record_run(..., dedupe_key=q)`.  |
| New        | `tests/test_search.py`                        | Cart pure function + cache round-trip + parser + supplier overlay.             |
| Edit       | `CHANGELOG.md`                                | Add entry under `[Unreleased] > Added` when actually built.                    |

No migrations besides #5. No new Makefile target required (optional `make
search Q="iphone"` wrapper later).

---

## Cart compare — the pure function

```
find_cheapest_per_store(
    items: list[str],
    cache_or_web: dict[str, list[PriceOffer]],
) -> {
    "store_totals":     {"Amazon": 490.50, "BestBuy": 500.00},
    "best_store":       "Amazon",
    "best_total":       490.50,
    "missing_coverage": {"Walmart": ["AA batteries"]},
}
```

**Rules:**
- Drop any store with <100% coverage of cart items from the ranking.
- Sum prices only at stores with full coverage.
- Pick the cheapest total.
- Surface every store that misses at least one item, so the user sees why
  a "great" store isn't in the verdict.

This is a pure function — no I/O — so it's trivial to unit-test.

---

## UI sketch

### Input section (above the table)

- Single-product text box (`st.text_input`)
- Cart text area (`st.text_area`, one item per line OR comma-separated)
- "Search" button

### Single mode — results table

```
| Store      | Price (USD) | URL                          | Supplier Cost* | Margin*  |
|------------|-------------|------------------------------|----------------|----------|
| Amazon     | $749.99     | https://amazon.com/...       | -              | -        |
| BestBuy    | $799.00     | https://bestbuy.com/...      | -              | -        |
| Walmart    | $809.00     | https://walmart.com/...      | -              | -        |
```

Sorted cheapest-first. USD-only.

### Cart mode — verdict + breakdown

```
🏆 Buy everything from Amazon — total $1,247.50

Per-store subtotals (100% cart coverage):
  Amazon   $1,247.50
  BestBuy  $1,289.10
  Walmart  $1,310.00

Stores missing items (excluded from ranking):
  Target   missing "AA batteries", "milk"
```

\* "Supplier Cost" and "Margin" columns only show when the product name
matches a glob pattern in `data/suppliers.csv` (helps for things like
"yoga mat" or "t-shirt"; doesn't help for "iphone 15" outside the
catalog).

---

## CLI subcommand

```
python -m trend_hunter.cli search "iphone 15"
  --json            # output pure JSON
  --days 1          # max cache age (default 1)
  --limit 20        # max results per store
```

Wrapped in `record_run("search", dedupe_key=query)` so it surfaces in
weekly `loop-report`.

---

## Risks / gotchas (already known)

- **Snippet misfires** — "`Save $50 on $499`" will parse as `$50` unless
  we guard with sane price range. Guard to $1–$9999 in v0.
- **Foreign currencies** — non-USD dropped silently in v0. Add later.
- **DuckDuckGo rate-limiting** — tenacity with `wait_exponential_jitter`
  settng 2-10s minimum if used as backup.
- **Cache TTL** — 24h before re-fetch (configurable through `TH_SEARCH_TTL_HOURS`).
- **Supplier overlay coverage** — only fires for items in catalog.
  Don't promise it works for arbitrary consumer products.

---

## Open questions to decide before building

1. Want a **price-trend graph** on top of the basic compare (e.g. "iPhone
   15 was $799 two weeks ago, $749 now")? Big scope, another `search_history` table.
2. Want **email/SMS alerts** when a watched item drops below a price?
3. Should the search box let you **exclude** certain stores
   (e.g. "no Amazon")?
4. Single page or split into two pages (Search vs. Cart)?
5. For Brave, do you have an API key already, or should I add the
   one-time `scripts/wire_brave.sh` wizard (analogous to `wire_telegram.sh`)?

---

## Estimated effort

v0 (single product + cart + cache + page + tests): **half a day to one day**.

Bigger items (price history, alerts, exclusions): scale linearly.

---

## Earlier work that *is* already shipped

For reference: the previous conversation session also **closed
LOOP_ENGINEERING gap #1** by adding forecast back-test
auto-resolution. That work is done; the 5 new tests pass
(93/93 total). Relevant files shipped:

- `trend_hunter/observe/calibrate.py` — added `auto_resolve_calibrations()`
- `trend_hunter/observe/constants.py` — added `calibrations_resolved`
  to `COUNTER_KEYS`
- `trend_hunter/intelligence/aggregator.py` — calls auto-resolve at end
  of `aggregate()`
- `trend_hunter/cli.py` — added `calibrate backfill --days N [--dry]`
- `trend_hunter/tests/test_calibrate.py` — 5 new tests
- `trend_hunter/Makefile` — added `calibrate-backfill` target
- `trend_hunter/CHANGELOG.md` — entry under `[Unreleased]`
- `trend_hunter/docs/LOOP_ENGINEERING.md` — closed-gap note

That work is independent of this shopping-search idea; both can ship
without touching each other.
