"""📊 Top 10 Insights — ranked products across 7 timeframes.

Features:
  - Top 10 products ranked by mathematical momentum score
  - 7 timeframes: hourly, daily, weekly, 2-week, monthly, 3-month, 6-month
  - Product links (clickable buy URLs), vendor info
  - Price history chart per product
  - Market sentiment indicators
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings

st.set_page_config(page_title="Top 10 · trend-hunter", page_icon="📊", layout="wide")
st.title("📊 Top 10 Insights")
st.caption("Products ranked by mathematical momentum score across all timeframes.")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()

# ── load data ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner="Loading insights...")
def _load_products():
    """Load all classified products with URLs from the products table."""
    try:
        return storage.query("""
            SELECT a.source, a.external_id, a.title, a.status,
                   a.slope_pct_per_day, a.n_points, a.window_start, a.window_end,
                   p.url, p.price, p.currency, p.captured_at
            FROM agg_product_status a
            LEFT JOIN products p ON a.source = p.source AND a.external_id = p.external_id
            WHERE p.price IS NOT NULL
            ORDER BY a.n_points DESC
        """)
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def _load_trend_history():
    """Load trend history for momentum calculation."""
    try:
        return storage.query("""
            SELECT source, external_id, title, status, slope_pct_per_day,
                   n_points, window_end, timeframe_days
            FROM trend_history
            ORDER BY source, external_id, timeframe_days, window_end DESC
        """)
    except Exception:
        return []


rows = _load_products()
history = _load_trend_history()

if not rows:
    st.warning("No data yet. The scheduler builds history automatically.")
    st.stop()

# ── calculate momentum scores ─────────────────────────────────────────────────
def _calc_momentum(product_key, history_rows):
    """Calculate momentum score (0-100) for a product across all timeframes."""
    product_history = [
        r for r in history_rows
        if r["source"] == product_key[0] and r["external_id"] == product_key[1]
    ]
    if not product_history:
        return 0.0, {}

    scores = {}
    for tf in [1, 7, 14, 30, 90]:
        tf_rows = [r for r in product_history if r["timeframe_days"] == tf]
        if tf_rows:
            latest = tf_rows[0]
            slope = float(latest.get("slope_pct_per_day") or 0) * 100
            n = latest.get("n_points", 0)
            scores[tf] = {"slope_pct": round(slope, 2), "n_points": n}

    # Velocity (average absolute slope across timeframes) × 40%
    slopes = [abs(s["slope_pct"]) for s in scores.values()]
    velocity = sum(slopes) / max(len(slopes), 1)
    velocity_score = min(velocity * 8, 40)  # cap at 40

    # Volume (observation count, max 20) × 25%
    max_n = max((s["n_points"] for s in scores.values()), default=1)
    volume_score = min(max_n / 20, 1) * 25

    # Recency (has recent data?) × 15%
    recency_score = 15 if scores else 0

    # Consistency (how many timeframes have data) × 20%
    consistency_score = (len(scores) / 5) * 20

    total = velocity_score + volume_score + recency_score + consistency_score
    return round(min(total, 100), 1), scores


# Build scored products list
scored = []
seen = set()
# Pre-group history for O(1) lookup per product
_history_by_key: dict = {}
for h in history:
    hk = (h["source"], h["external_id"])
    if hk not in _history_by_key:
        _history_by_key[hk] = []
    _history_by_key[hk].append(h)

for r in rows:
    key = (r["source"], r["external_id"])
    if key in seen:
        continue
    seen.add(key)
    momentum, tf_scores = _calc_momentum(key, _history_by_key.get(key, []))
    scored.append({
        "source": r["source"],
        "external_id": r["external_id"],
        "title": r["title"] or r["external_id"],
        "price": r.get("price"),
        "url": r.get("url") or "",
        "status": r.get("status", "unknown"),
        "momentum": momentum,
        "points": r.get("n_points", 0),
        **{f"tf_{k}d": v["slope_pct"] for k, v in tf_scores.items()},
    })

scored.sort(key=lambda x: x["momentum"], reverse=True)

# ── sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.header("Filters")
all_sources = sorted({r["source"] for r in scored})
sel_source = st.sidebar.multiselect("Source", all_sources, default=all_sources)
top_n = st.sidebar.slider("Top N", 5, 50, 10)

filtered = [r for r in scored if r["source"] in sel_source][:top_n]

# ── KPI cards ─────────────────────────────────────────────────────────────────
st.subheader("📊 Market Overview")
total_prod = len(scored)
rising = sum(1 for r in scored if r.get("status") == "rising")
falling = sum(1 for r in scored if r.get("status") == "declining")
has_links = sum(1 for r in scored if r.get("url"))

k1, k2, k3, k4 = st.columns(4)
k1.metric("📦 Products", total_prod)
k2.metric("🟢 Rising", rising)
k3.metric("🔴 Falling", falling)
k4.metric("🔗 With Buy Links", has_links)

st.divider()

# ── 🏆 TOP 10 TABLE ───────────────────────────────────────────────────────────
st.subheader(f"🏆 Top {top_n} Products")
st.caption("Ranked by mathematical momentum score (velocity × volume × consistency × recency)")

if filtered:
    table_data = []
    for i, p in enumerate(filtered, 1):
        url = p.get("url", "")
        url_display = f"[BUY →]({url})" if url else "—"
        vendor = p["source"]

        row = {
            "Rank": i,
            "Product": str(p["title"])[:40],
            "🏪 Vendor": vendor,
            "💰 Price": f"${float(p['price']):.2f}" if p.get("price") else "—",
            "🔗 Link": url_display,
            "⚡ Momentum": f"{p['momentum']}/100",
        }
        # Add timeframe columns
        for tf_label, tf_key in [("24h", "tf_1d"), ("7d", "tf_7d"), ("30d", "tf_30d"), ("90d", "tf_90d")]:
            val = p.get(tf_key)
            if val is not None:
                row[tf_label] = f"{val:+.2f}%"
            else:
                row[tf_label] = "—"

        table_data.append(row)

    df = pd.DataFrame(table_data)
    st.dataframe(df, use_container_width=True, hide_index=True, height=min(38 * len(df) + 38, 500))
else:
    st.info("No products match your filters.")

st.divider()

# ── 📈 PRODUCT DETAIL ─────────────────────────────────────────────────────────
st.subheader("📈 Product Deep Dive")
product_options = [f"#{i+1} {p['title'][:50]} ({p['source']})" for i, p in enumerate(filtered[:30])]
if product_options:
    picked = st.selectbox("Select a product to analyze", product_options, key="top10_detail")
    if picked:
        # Extract the product from filtered list
        idx = product_options.index(picked)
        product = filtered[idx]

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"**🏪 Vendor:** `{product['source']}`")
            price = product.get("price")
            if price:
                st.metric("💰 Current Price", f"${float(price):.2f}")
        with col2:
            st.markdown(f"**⚡ Momentum:** `{product['momentum']}/100`")
            st.metric("📊 Data Points", product.get("points", 0))
        with col3:
            url = product.get("url", "")
            if url:
                st.markdown(f"**🔗 [Open Product Page →]({url})**")
            st.metric("📈 Status", str(product.get("status", "?")).upper())

        # Timeframe breakdown
        st.markdown("#### ⏱️ Timeframe Breakdown")
        tf_cols = st.columns(5)
        tf_labels = [("24h", "tf_1d"), ("7d", "tf_7d"), ("14d", "tf_14d"), ("30d", "tf_30d"), ("90d", "tf_90d")]
        for col, (label, key) in zip(tf_cols, tf_labels, strict=False):
            val = product.get(key)
            if val is not None:
                col.metric(label, f"{val:+.2f}%", delta_color="normal")
            else:
                col.metric(label, "N/A")

        # Price chart
        st.markdown("#### 📈 Price History")
        try:
            pts = storage.query(
                "SELECT captured_at, price FROM products WHERE source=? AND external_id=? AND price IS NOT NULL ORDER BY captured_at",
                (product["source"], product["external_id"]),
            )
            if pts:
                df_pts = pd.DataFrame(pts)
                df_pts["captured_at"] = pd.to_datetime(df_pts["captured_at"])
                df_pts = df_pts.drop_duplicates("captured_at").set_index("captured_at")
                st.line_chart(df_pts, height=300, use_container_width=True)
        except Exception:
            st.info("Price history not available yet.")
