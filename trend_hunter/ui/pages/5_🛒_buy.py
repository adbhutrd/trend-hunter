"""🛒 Shop — Browse products, compare prices, buy with one click."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import PRICE_BANDS, apply_professional_theme

st.set_page_config(page_title="Shop · trend-hunter", page_icon="🛒", layout="wide")
apply_professional_theme()
st.title("🛒 Browse Products")
st.caption("All products with prices and direct buy links. Click any link to open the store page.")

# ── SOURCE FILTER ────────────────────────────────────────────────────────────
source_filter = st.radio(
    "📡 Data Source",
    ["🔄 All Sources", "🛍️ Shopify", "📦 eBay"],
    horizontal=True,
    key="buy_source",
)
source_label = "" if source_filter.startswith("🔄") else ("shopify" if "Shopify" in source_filter else "ebay")

st_autorefresh(interval=60_000, key="buy_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


@st.cache_data(ttl=30, show_spinner="Loading products...")
def _load_products(source: str = ""):
    src_clause = f"AND source = '{source}'" if source else ""
    try:
        return storage.query(f"""
            SELECT source, external_id, title, price, currency, url, captured_at
            FROM products
            WHERE url IS NOT NULL AND url != '' AND price IS NOT NULL {src_clause}
            ORDER BY captured_at DESC
        """)
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def _vendor_stats():
    try:
        return storage.query("""
            SELECT source, count(DISTINCT external_id) as products,
                   round(avg(price), 2) as avg_price,
                   round(min(price), 2) as min_price,
                   round(max(price), 2) as max_price
            FROM products WHERE price IS NOT NULL
            GROUP BY source ORDER BY products DESC
        """)
    except Exception:
        return []


rows = _load_products(source=source_label)
vendors = _vendor_stats()

if not rows:
    st.warning("⏳ No products with prices yet. Products appear after the first scan cycle.")
    st.stop()

# ── FILTERS ───────────────────────────────────────────────────────────────────
st.sidebar.header("Filters")

prices = [float(r["price"]) for r in rows if r.get("price")]
if prices:
    min_p, max_p = min(prices), max(prices)
    price_range = st.sidebar.slider("💰 Price Range", float(min_p), float(max_p),
                                    (float(min_p), float(max_p)))
else:
    price_range = (0, 1000)

search = st.sidebar.text_input("🔍 Search Products", placeholder="Type product name...")

# ── OVERVIEW ──────────────────────────────────────────────────────────────────
st.markdown("### 📊 Marketplace Overview")
total = len({(r["source"], r["external_id"]) for r in rows})
c1, c2, c3, c4 = st.columns(4)
c1.metric("🛍️ **Total Products**", total)
c2.metric("🔗 **With Buy Links**", total)
c3.metric("🏪 **Stores**", len(vendors))
c4.metric("💰 **Avg Price**", f"${sum(prices)/len(prices):.2f}" if prices else "—")

st.divider()

# ── PRODUCT LIST BY PRICE ────────────────────────────────────────────────────
st.markdown("### 🛍️ Browse by Price Category")
st.caption("Products grouped by price range. Click any link to open the store page.")

# Deduplicate rows once
seen_ids = set()
unique_rows = []
for r in rows:
    key = (r["source"], r["external_id"])
    if key not in seen_ids:
        seen_ids.add(key)
        unique_rows.append(r)

cat_tabs = st.tabs([b[0] for b in PRICE_BANDS])

for tab_idx, (_, lo, hi) in enumerate(PRICE_BANDS):
    with cat_tabs[tab_idx]:
        # Filter within price range + sidebar filters
        band_rows = [
            r for r in unique_rows
            if lo <= float(r.get("price") or 0) < hi
            and price_range[0] <= float(r.get("price") or 0) <= price_range[1]
        ]
        if search:
            band_rows = [r for r in band_rows if search.lower() in str(r["title"]).lower()]

        if not band_rows:
            st.caption("No products in this range match your filters.")
            continue

        st.caption(f"Showing {min(len(band_rows), 30)} of {len(band_rows)} products")

        data = []
        seen_titles = set()
        for r in band_rows:
            t = (r["title"] or r["external_id"]).lower().strip()
            if t in seen_titles:
                continue
            seen_titles.add(t)
            if len(data) >= 30:
                break
            data.append({
                "Product": (r["title"] or r["external_id"])[:45],
                "🏪 Store": r["source"].title(),
                "💰 Price": f"${float(r['price']):.2f}" if r.get("price") else "—",
                "💱 Currency": r.get("currency", "USD"),
                "🔗": r.get("url", ""),
            })

        st.dataframe(
            pd.DataFrame(data), width="stretch", hide_index=True,
            height=min(42 * len(data) + 42, 600),
            column_config={"🔗": st.column_config.LinkColumn("Buy Now", display_text="🛒 BUY NOW →")},
        )

st.divider()

# ── STORE DIRECTORY ───────────────────────────────────────────────────────────
st.markdown("### 🏪 Store Directory")
st.caption("All stores ranked by number of products and price range.")

if vendors:
    data = []
    for v in vendors:
        data.append({
            "🏪 Store": v["source"].title(),
            "📦 Products": v["products"],
            "💰 Avg Price": f"${v['avg_price']:.2f}",
            "📉 Lowest": f"${v['min_price']:.2f}",
            "📈 Highest": f"${v['max_price']:.2f}",
        })
    st.dataframe(pd.DataFrame(data), width="stretch", hide_index=True,
                 height=min(42 * len(data) + 42, 250))
else:
    st.info("Store data building up...")

st.divider()

# ── QUICK SEARCH ──────────────────────────────────────────────────────────────
st.markdown("### 🔍 Quick Product Search")
st.caption("Find any product by name or paste a URL.")

lookup = st.text_input("Search by product name or URL", key="buy_lookup",
                       placeholder="e.g. 'Nike shoes' or paste a product URL")
if lookup:
    try:
        matches = storage.query(
            "SELECT source, external_id, title, price, currency, url FROM products "
            "WHERE title ILIKE ? OR url ILIKE ? LIMIT 10",
            (f"%{lookup}%", f"%{lookup}%"),
        )
        if matches:
            for m in matches:
                st.markdown(f"**{m['title']}**")
                price_str = f"💰 ${float(m['price']):.2f}" if m.get("price") else ""
                st.caption(f"🏪 {m['source'].title()} {price_str}")
                if m.get("url"):
                    st.markdown(f"🔗 **[Open Store Page →]({m['url']})**")
                st.divider()
        else:
            st.info("No products found. Try a different search term.")
    except Exception:
        st.info("Search not available yet. Try again after the next scan cycle.")

st.caption(f"Updated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
