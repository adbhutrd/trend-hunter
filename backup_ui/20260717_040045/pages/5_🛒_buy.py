"""🛒 BUY & VENDORS — Every product with a purchase link + vendor directory + price comparison.

Your marketplace hub:
  - 🔗 All Buyable Products — every product with clickable store link
  - 🏪 Vendor Directory — every source with stats and reliability
  - 💲 Price Comparison — find the cheapest vendor
  - 🔍 Smart Search — look up any product by name or paste URL
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import apply_professional_theme

st.set_page_config(page_title="Buy & Vendors · trend-hunter", page_icon="🛒", layout="wide")
apply_professional_theme()
st.title("🛒 Buy & Vendors")
st.caption("Products with direct purchase links. Click any link to open the store page.")

st_autorefresh(interval=60_000, key="buy_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


# ── DATA LOADERS ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner="Loading buyable products...")
def _load_buyable():
    """All products that have URLs + prices."""
    try:
        return storage.query(
            """
            SELECT source, external_id, title, price, currency, url, captured_at
            FROM products
            WHERE url IS NOT NULL AND url != '' AND price IS NOT NULL
            ORDER BY captured_at DESC
        """
        )
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def _vendor_stats():
    """Per-vendor aggregate stats."""
    try:
        return storage.query(
            """
            SELECT source,
                   count(DISTINCT external_id) as products,
                   round(avg(price), 2) as avg_price,
                   round(min(price), 2) as min_price,
                   round(max(price), 2) as max_price,
                   round(stddev(price), 2) as price_volatility,
                   count(*) as observations
            FROM products
            WHERE price IS NOT NULL
            GROUP BY source
            ORDER BY products DESC
        """
        )
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _price_comparison():
    """Products that might exist across multiple vendors (by title similarity)."""
    try:
        return storage.query(
            """
            SELECT source, external_id, title, price, url, captured_at
            FROM products
            WHERE price IS NOT NULL AND title IS NOT NULL
            ORDER BY title
        """
        )
    except Exception:
        return []


# ── LOAD DATA ─────────────────────────────────────────────────────────────────
rows = _load_buyable()
vendors = _vendor_stats()
all_products = _price_comparison()

if not rows:
    st.warning("No products with purchase links yet. Data builds after each scan cycle.")
    st.stop()

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
st.sidebar.header("Filters")
all_sources = sorted({r["source"] for r in rows})
sel_source = st.sidebar.multiselect("🏪 Vendor", all_sources, default=all_sources)

prices = [float(r["price"]) for r in rows if r.get("price")]
if prices:
    min_p, max_p = min(prices), max(prices)
    price_range = st.sidebar.slider("💰 Price range", float(min_p), float(max_p), (float(min_p), float(max_p)))
else:
    price_range = (0, 1000)

search = st.sidebar.text_input("🔍 Search products", placeholder="Type product name...")

# ── SECTION 1: OVERVIEW ──────────────────────────────────────────────────────
st.subheader("📊 Marketplace Overview")
total = len({(r["source"], r["external_id"]) for r in rows})
v1, v2, v3, v4 = st.columns(4)
v1.metric("🛍️ Total Products", total)
v2.metric("🔗 With Links", total)
v3.metric("🏪 Vendors", len(vendors))
v4.metric("💰 Avg Price", f"${sum(prices) / len(prices):.2f}" if prices else "—")

st.divider()

# ── SECTION 2: ALL BUYABLE PRODUCTS ──────────────────────────────────────────
st.subheader("All Buyable Products")

# Filter
filtered = [
    r for r in rows
    if r["source"] in sel_source
    and price_range[0] <= float(r.get("price") or 0) <= price_range[1]
]
if search:
    filtered = [r for r in filtered if search.lower() in str(r["title"]).lower()]

# Deduplicate
seen = set()
unique = []
for r in filtered:
    key = (r["source"], r["external_id"])
    if key not in seen:
        seen.add(key)
        unique.append(r)
filtered = unique

st.caption(f"Showing {len(filtered)} of {total} products")

if filtered:
    table_data = []
    for i, r in enumerate(filtered[:100], 1):
        url = r.get("url", "") or ""
        table_data.append({
            "#": i,
            "Product": (r["title"] or r["external_id"])[:45],
            "🏪 Vendor": r["source"],
            "💰 Price": f"${float(r['price']):.2f}" if r.get("price") else "—",
            "💱 Cur": r.get("currency", "USD"),
            "🔗 Buy Link": url,
        })

    df = pd.DataFrame(table_data)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=min(40 * len(df) + 40, 600),
        column_config={
            "🔗 Buy Link": st.column_config.LinkColumn(
                "🔗 Buy Link",
                display_text="🛒 BUY NOW →",
                width="large",
            ),
        },
    )
else:
    st.info("No products match your filters.")

st.divider()

# ── SECTION 3: 🏪 VENDOR DIRECTORY ───────────────────────────────────────────
st.subheader("🏪 Vendor Directory")
st.caption("All vendors/sources ranked by number of products and price range.")

if vendors:
    vdata = []
    for v in vendors:
        vdata.append({
            "🏪 Vendor": v["source"],
            "📦 Products": v["products"],
            "💰 Avg Price": f"${v['avg_price']:.2f}",
            "📉 Min": f"${v['min_price']:.2f}",
            "📈 Max": f"${v['max_price']:.2f}",
            "📊 Range": f"${v['max_price'] - v['min_price']:.2f}",
            "📉 Volatility": f"${v.get('price_volatility', 0):.2f}" if v.get("price_volatility") else "—",
            "📡 Observations": f"{v['observations']:,}",
        })

    st.dataframe(
        pd.DataFrame(vdata),
        use_container_width=True,
        hide_index=True,
        height=min(40 * len(vdata) + 40, 300),
    )

st.divider()

# ── SECTION 4: 💲 PRICE COMPARISON ───────────────────────────────────────────
st.subheader("💲 Price Comparison")
st.caption("Find the best price for similar products across vendors.")

if all_products and len(all_products) >= 5:
    # Simple heuristic: group products with similar first 30 chars of title
    from collections import defaultdict

    groups = defaultdict(list)
    for r in all_products:
        title_key = str(r.get("title", ""))[:30].strip().lower()
        if title_key:
            groups[title_key].append(r)

    # Find groups with multiple vendors
    multi_vendor = {k: v for k, v in groups.items() if len({r["source"] for r in v}) > 1}

    if multi_vendor:
        comp_data = []
        for title_key, items in sorted(multi_vendor.items(), key=lambda x: len(x[1]), reverse=True)[:20]:
            min_price = min(float(r["price"]) for r in items if r.get("price"))
            max_price = max(float(r["price"]) for r in items if r.get("price"))
            for r in items:
                price = float(r["price"]) if r.get("price") else 0
                savings = max_price - price if max_price > 0 else 0
                url = r.get("url", "") or ""

                comp_data.append({
                    "Product": title_key[:35],
                    "🏪 Vendor": r["source"],
                    "💰 Price": f"${price:.2f}",
                    "💵 Savings vs Max": f"${savings:.2f}" if savings > 0 else "—",
                    "🔗": url,
                })

        if comp_data:
            st.dataframe(
                pd.DataFrame(comp_data),
                use_container_width=True,
                hide_index=True,
                height=min(40 * len(comp_data) + 40, 500),
                column_config={
                    "🔗": st.column_config.LinkColumn("Link", display_text="View →"),
                },
            )
        else:
            st.info("No multi-vendor products found for comparison.")
    else:
        st.info("Not enough overlapping products across vendors for comparison yet.")
else:
    st.info("Need more product data across vendors for price comparison.")

st.divider()

# ── SECTION 5: 🔍 QUICK LOOKUP ──────────────────────────────────────────────
st.subheader("🔍 Quick Lookup")
st.caption("Paste a product name or URL to find it instantly.")

lookup = st.text_input("Search by name or paste URL", key="buy_lookup")
if lookup:
    try:
        matches = storage.query(
            "SELECT source, external_id, title, price, currency, url FROM products "
            "WHERE title ILIKE ? OR url ILIKE ? LIMIT 10",
            (f"%{lookup}%", f"%{lookup}%"),
        )
        if matches:
            for m in matches:
                with st.container(border=True):
                    st.markdown(f"**{m['title']}**")
                    st.caption(f"🏪 `{m['source']}` — 💰 ${float(m['price']):.2f}" if m.get("price") else f"🏪 `{m['source']}`")
                    url = m.get("url", "")
                    if url:
                        st.markdown(f"🔗 **[Buy →]({url})**")
        else:
            st.info("No products found. Try a different search term.")
    except Exception:
        st.info("Search not available yet. Try again after the next scan cycle.")

# ── TIMESTAMP ─────────────────────────────────────────────────────────────────
st.caption(f"\nUpdated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
