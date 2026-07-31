"""🔥 Trending — What's hot right now. Simple, clear, actionable."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import PRICE_BANDS, apply_professional_theme

st.set_page_config(page_title="Trending · trend-hunter", page_icon="🔥", layout="wide")
apply_professional_theme()
st.title("🔥 What's Trending Now")
st.caption("Products with the strongest movement right now. Updated every 30 seconds.")

# ── SOURCE FILTER ────────────────────────────────────────────────────────────
source_filter = st.radio(
    "📡 Data Source",
    ["🔄 All Sources", "🛍️ Shopify", "📦 eBay"],
    horizontal=True,
    key="hot_source",
)
source_label = "" if source_filter.startswith("🔄") else ("shopify" if "Shopify" in source_filter else "ebay")

st_autorefresh(interval=30_000, key="hot_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


# ── DATA LOADERS ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=20, show_spinner="Loading trending products...")
def _load_momentum(source: str = "", limit: int = 30):
    src_clause = f"AND a.source = '{source}'" if source else ""
    try:
        return storage.query(f"""
            SELECT a.source, a.external_id, a.title, a.status,
                   a.slope_pct_per_day, a.n_points, a.timeframe_days,
                   p.price, p.url
            FROM agg_product_status a
            LEFT JOIN (
                SELECT source, external_id, FIRST(price) as price,
                       FIRST(url) as url
                FROM products WHERE price IS NOT NULL
                GROUP BY source, external_id
            ) p ON a.source = p.source AND a.external_id = p.external_id
            WHERE a.n_points >= 1 {src_clause}
            ORDER BY ABS(a.slope_pct_per_day) DESC
            LIMIT {int(limit)}
        """)
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_new_finds(source: str = "", days: int = 7):
    src_clause = f"AND source = '{source}'" if source else ""
    try:
        return storage.query(f"""
            SELECT source, external_id, title, price, url, captured_at
            FROM products
            WHERE captured_at >= now() - INTERVAL {int(days)} DAY
            AND title IS NOT NULL {src_clause}
            ORDER BY captured_at DESC LIMIT 20
        """)
    except Exception:
        return []


@st.cache_data(ttl=20, show_spinner=False)
def _load_by_price_range(lo: float, hi: float, source: str = "", limit: int = 10):
    """Return top unique products in a price range, ordered by momentum."""
    src_clause = f"AND a.source = '{source}'" if source else ""
    try:
        rows = storage.query(f"""
            SELECT a.title, p.price, p.url, a.status,
                   MAX(a.slope_pct_per_day) as best_slope, p.source
            FROM agg_product_status a
            JOIN products p ON a.external_id = p.external_id AND a.source = p.source
            WHERE a.status = 'rising'
              AND p.price IS NOT NULL AND p.price > {float(lo)} AND p.price < {float(hi)}
              AND a.n_points >= 1 {src_clause}
            GROUP BY a.title, p.price, p.url, a.status, p.source
            ORDER BY MAX(ABS(a.slope_pct_per_day)) DESC
            LIMIT {int(limit)}
        """)
        if rows:
            return rows
    except Exception as e:
        st.warning(f"Price category query (rising): {e}")

    try:
        # Fallback: show best momentum regardless of status
        return storage.query(f"""
            SELECT a.title, p.price, p.url, a.status,
                   MAX(a.slope_pct_per_day) as best_slope, p.source
            FROM agg_product_status a
            JOIN products p ON a.external_id = p.external_id AND a.source = p.source
            WHERE p.price IS NOT NULL AND p.price > {float(lo)} AND p.price < {float(hi)}
              AND a.n_points >= 1 {src_clause}
            GROUP BY a.title, p.price, p.url, a.status, p.source
            ORDER BY MAX(ABS(a.slope_pct_per_day)) DESC
            LIMIT {int(limit)}
        """)
    except Exception as e:
        st.warning(f"Price category query (fallback): {e}")
        return []


@st.cache_data(ttl=15, show_spinner=False)
def _load_counts(source: str = ""):
    src_clause = f"AND source = '{source}'" if source else ""
    try:
        return storage.query(
            f"SELECT status, count(*) as n FROM agg_product_status "
            f"WHERE status IN ('rising','declining','stable','unknown') {src_clause} "
            "GROUP BY status ORDER BY n DESC"
        )
    except Exception:
        return []


# ── MARKET OVERVIEW ───────────────────────────────────────────────────────────
st.markdown("### 📊 Market Overview")

status_counts = _load_counts(source=source_label)
if status_counts:
    smap = {r["status"]: r["n"] for r in status_counts}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📈 **Rising**", smap.get("rising", 0), help="Products going up in price")
    c2.metric("📉 **Declining**", smap.get("declining", 0), help="Products going down in price")
    c3.metric("🔵 **Stable**", smap.get("stable", 0), help="Products with steady prices")
    c4.metric("⚪ **Unknown**", smap.get("unknown", 0), help="Products not yet classified")
else:
    st.info("⏳ Data building up after first scan...")

st.divider()

# ── TRENDING PRODUCTS ────────────────────────────────────────────────────────
st.markdown("### 🏆 Top Trending Products")
st.caption("Products with the strongest price movement. Green = rising, Red = declining.")

leaders = _load_momentum(source=source_label, limit=30)
if not leaders:
    st.info("⏳ No trend data yet. Products appear after the first scan cycle.")
else:
    # Group by product, show best slope
    pmap = {}
    for r in leaders:
        key = (r["source"], r["external_id"])
        if key not in pmap:
            pmap[key] = {
                "title": (r["title"] or r["external_id"])[:50],
                "source": r["source"],
                "price": r.get("price"),
                "url": r.get("url") or "",
                "status": r.get("status", "?"),
                "best_slope": 0.0,
                "best_tf": 0,
                "all_slopes": [],
            }
        m = pmap[key]
        slope = float(r.get("slope_pct_per_day", 0) or 0) * 100
        m["all_slopes"].append(slope)
        if abs(slope) > abs(m["best_slope"]):
            m["best_slope"] = slope
            m["best_tf"] = r.get("timeframe_days", 0)

    scored = sorted(pmap.values(), key=lambda x: abs(x["best_slope"]), reverse=True)

    # Split into rising and declining
    rising = [p for p in scored if p["best_slope"] > 0][:10]
    declining = [p for p in scored if p["best_slope"] < 0][:10]

    tab1, tab2 = st.tabs(["📈 Rising (Hot)", "📉 Declining (Cold)"])

    with tab1:
        if rising:
            data = []
            for i, p in enumerate(rising, 1):
                data.append({
                    "#": i,
                    "Product": p["title"],
                    "📈 Momentum": f"+{p['best_slope']:.1f}%/day",
                    "💰 Price": f"${float(p['price']):.2f}" if p.get("price") else "—",
                    "🏪 Store": p["source"].title(),
                    "🔗": p.get("url", ""),
                })
            st.dataframe(
                pd.DataFrame(data), width="stretch", hide_index=True,
                height=min(50 * len(data) + 42, 550),
                column_config={"🔗": st.column_config.LinkColumn("Buy", display_text="🛒 →")},
            )
        else:
            st.info("No rising products yet — data builds after each scan.")

    with tab2:
        if declining:
            data = []
            for i, p in enumerate(declining, 1):
                data.append({
                    "#": i,
                    "Product": p["title"],
                    "📉 Momentum": f"{p['best_slope']:.1f}%/day",
                    "💰 Price": f"${float(p['price']):.2f}" if p.get("price") else "—",
                    "🏪 Store": p["source"].title(),
                    "🔗": p.get("url", ""),
                })
            st.dataframe(
                pd.DataFrame(data), width="stretch", hide_index=True,
                height=min(50 * len(data) + 42, 550),
                column_config={"🔗": st.column_config.LinkColumn("Buy", display_text="🛒 →")},
            )
        else:
            st.info("No declining products yet.")

st.divider()

# ── BY PRICE CATEGORY ────────────────────────────────────────────────────────
st.markdown("### 💰 Top Products by Price Category")
st.caption("The best momentum products in each price range. 10 per category. Links open the real store page.")

for label, lo, hi in PRICE_BANDS:
    st.markdown(f"#### {label}")
    rows = _load_by_price_range(lo, hi, source=source_label)
    if not rows:
        st.caption("No products in this range yet.")
        continue

    pdata = []
    # Deduplicate by title
    seen_titles = set()
    for r in rows:
        title = (r["title"] or "").strip()
        if not title or title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())

        price = float(r["price"]) if r.get("price") else None
        slp = float(r["best_slope"] or 0) * 100
        profit = round(price * 0.08, 2) if price else None
        trend_icon = "📈" if r["status"] == "rising" else ("📉" if r["status"] == "declining" else "🔵")

        pdata.append({
            "Product": title[:45],
            "Price": f"${price:.2f}" if price else "—",
            "Momentum": f"{slp:+.1f}%/d",
            "Profit @8%": f"${profit:.2f}" if profit is not None else "—",
            "⚡": trend_icon,
            "🔗": r.get("url", ""),
        })

    if pdata:
        df = pd.DataFrame(pdata)
        st.dataframe(
            df, width="stretch", hide_index=True,
            height=min(48 * len(pdata) + 42, 520),
            column_config={
                "🔗": st.column_config.LinkColumn("Store", display_text="🛒 Open →"),
            },
        )

st.divider()

# ── NEW FINDS ─────────────────────────────────────────────────────────────────
st.markdown("### 🆕 New Products Found (Last 7 Days)")

new_finds = _load_new_finds(source=source_label, days=7)
if new_finds:
    seen = set()
    unique = []
    for r in new_finds:
        key = (r["source"], r["external_id"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    data = []
    for i, r in enumerate(unique[:12], 1):
        data.append({
            "#": i,
            "Product": (r["title"] or r["external_id"])[:45],
            "🏪 Store": r["source"].title(),
            "💰 Price": f"${float(r['price']):.2f}" if r.get("price") else "—",
            "🔗": r.get("url", ""),
        })

    st.dataframe(
        pd.DataFrame(data), width="stretch", hide_index=True,
        height=min(50 * len(data) + 42, 500),
        column_config={"🔗": st.column_config.LinkColumn("View", display_text="🔗 Open →")},
    )
else:
    st.info("No new products this week.")

st.caption(f"Updated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
