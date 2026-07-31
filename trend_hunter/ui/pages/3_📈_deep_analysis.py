"""📈 DEEP ANALYSIS — Category explorer, cross-timeframe matrix, per-product deep dive, market sentiment.

The analytical powerhouse. Drill into any product across timeframes:
  - 📂 Category/Source Explorer — browse products grouped by source
  - ⏱️ Cross-Timeframe Matrix — every product × 7 timeframes in one view
  - 🔍 Per-Product Deep Dive — click any product for full history + chart
  - 📊 Market Sentiment Dashboard — aggregate signals across all products
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import apply_professional_theme

st.set_page_config(page_title="Deep Analysis · trend-hunter", page_icon="📈", layout="wide")
apply_professional_theme()
st.title("📈 Deep Analysis")
st.caption("Cross-timeframe analysis, category breakdown, and per-product deep dives.")

st_autorefresh(interval=60_000, key="deep_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


# ── DATA LOADERS ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner="Loading categories...")
def _load_category_summary():
    """Per-source summary with product counts and price stats."""
    try:
        return storage.query(
            """
            SELECT source,
                   count(DISTINCT external_id) as products,
                   round(avg(price), 2) as avg_price,
                   round(min(price), 2) as min_price,
                   round(max(price), 2) as max_price,
                   count(*) as observations
            FROM products
            WHERE price IS NOT NULL
            GROUP BY source
            ORDER BY products DESC
        """
        )
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner="Loading classified products...")
def _load_all_classified():
    """All classified products with their per-timeframe data."""
    try:
        return storage.query(
            """
            SELECT a.source, a.external_id, a.title, a.status,
                   a.slope_pct_per_day, a.n_points, a.timeframe_days,
                   a.window_start, a.window_end,
                   p.price, p.url
            FROM agg_product_status a
            LEFT JOIN (
                SELECT source, external_id, FIRST(price) as price,
                       FIRST(url) as url
                FROM products WHERE price IS NOT NULL
                GROUP BY source, external_id
            ) p ON a.source = p.source AND a.external_id = p.external_id
            ORDER BY ABS(a.slope_pct_per_day) DESC
        """
        )
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_product_history(source: str, external_id: str):
    """Full price history for a specific product."""
    try:
        return storage.query(
            "SELECT captured_at, price FROM products "
            "WHERE source=? AND external_id=? AND price IS NOT NULL "
            "ORDER BY captured_at ASC",
            (source, external_id),
        )
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def _load_sentiment_summary():
    """Aggregate market sentiment stats."""
    out = {}
    try:
        r = storage.query(
            "SELECT status, count(*) as n FROM agg_product_status "
            "GROUP BY status"
        )
        out["status_counts"] = {row["status"]: row["n"] for row in r}
    except Exception:
        out["status_counts"] = {}
    try:
        r = storage.query(
            "SELECT timeframe_days, status, count(*) as n "
            "FROM agg_product_status GROUP BY timeframe_days, status "
            "ORDER BY timeframe_days"
        )
        out["tf_status"] = r
    except Exception:
        out["tf_status"] = []
    try:
        r = storage.query(
            "SELECT pattern, count(*) as n "
            "FROM cross_timeframe_patterns GROUP BY pattern ORDER BY n DESC"
        )
        out["patterns"] = {row["pattern"]: row["n"] for row in r}
    except Exception:
        out["patterns"] = {}
    return out


# ── SIDEBAR NAVIGATION ───────────────────────────────────────────────────────
st.sidebar.header("🔍 Analysis Tools")
view_mode = st.sidebar.radio(
    "View",
    ["📂 Category Explorer", "⏱️ Cross-Timeframe Matrix", "🔍 Product Deep Dive", "📊 Sentiment Dashboard"],
    key="deep_view",
)


# ═══════════════════════════════════════════════════════════════════════════════
# 📂 CATEGORY EXPLORER
# ═══════════════════════════════════════════════════════════════════════════════
if view_mode == "📂 Category Explorer":
    st.subheader("📂 Category / Source Explorer")
    st.caption("Browse products grouped by source/vendor. Each source is a separate category.")

    cats = _load_category_summary()
    if not cats:
        st.info("No category data yet.")
    else:
        # Source overview cards
        cat_cols = st.columns(min(3, len(cats)))
        for i, cat in enumerate(cats[:3]):
            with cat_cols[i]:
                st.metric(
                    f"🏪 {cat['source']}",
                    f"{cat['products']} products",
                    f"${cat['avg_price']:.2f} avg · ${cat['min_price']:.2f}–${cat['max_price']:.2f}",
                )

        # Per-source product tables
        for cat in cats:
            with st.expander(f"🏪 {cat['source']} — {cat['products']} products"):
                try:
                    products = storage.query(
                        "SELECT a.source, a.external_id, a.title, a.status, "
                        "a.slope_pct_per_day, a.timeframe_days, "
                        "p.price, p.url "
                        "FROM agg_product_status a "
                        "LEFT JOIN (SELECT source, external_id, FIRST(price) as price, "
                        "FIRST(url) as url FROM products WHERE price IS NOT NULL "
                        "GROUP BY source, external_id) p "
                        "ON a.source=p.source AND a.external_id=p.external_id "
                        "WHERE a.source=? "
                        "ORDER BY ABS(a.slope_pct_per_day) DESC LIMIT 30",
                        (cat["source"],),
                    )
                    if products:
                        # Group by product
                        pmap = {}
                        for r in products:
                            key = (r["external_id"])
                            if key not in pmap:
                                pmap[key] = {
                                    "title": (r["title"] or r["external_id"])[:40],
                                    "price": r.get("price"),
                                    "url": r.get("url") or "",
                                    "status": r.get("status", "?"),
                                    "best_slope": 0.0,
                                }
                            slope = float(r.get("slope_pct_per_day", 0) or 0) * 100
                            if abs(slope) > abs(pmap[key]["best_slope"]):
                                pmap[key]["best_slope"] = slope

                        pdata = []
                        for _, pm in pmap.items():
                            pdata.append({
                                "Product": pm["title"],
                                "📈 Slope%": f"{pm['best_slope']:+.2f}%",
                                "💰 Price": f"${float(pm['price']):.2f}" if pm["price"] else "—",
                                "🔗": pm.get("url", ""),
                                "Status": pm["status"].upper(),
                            })
                        st.dataframe(
                            pd.DataFrame(pdata),
                            width="stretch",
                            hide_index=True,
                            height=min(38 * len(pdata) + 38, 400),
                            column_config={
                                "🔗": st.column_config.LinkColumn("🔗 Link", display_text="🔗"),
                            },
                        )
                except Exception:
                    st.info(f"Products loading for {cat['source']}...")

# ═══════════════════════════════════════════════════════════════════════════════
# ⏱️ CROSS-TIMEFRAME MATRIX
# ═══════════════════════════════════════════════════════════════════════════════
elif view_mode == "⏱️ Cross-Timeframe Matrix":
    st.subheader("⏱️ Cross-Timeframe Analysis Matrix")
    st.caption("Every product × 7 timeframes — shows slope % change across all windows side-by-side.")

    rows = _load_all_classified()
    if not rows:
        st.info("No classified data yet.")
    else:
        # Build matrix: products × timeframes
        matrix: dict = {}
        tf_labels = {1: "1h", 7: "24h", 14: "7d", 30: "14d", 90: "30d", 180: "90d"}
        tf_order = [1, 7, 14, 30, 90, 180]

        for r in rows:
            key = (r["source"], r["external_id"])
            if key not in matrix:
                matrix[key] = {
                    "title": (r["title"] or r["external_id"])[:40],
                    "source": r["source"],
                    "price": r.get("price"),
                    "url": r.get("url") or "",
                    "status": r.get("status", "?"),
                    "tfs": {},
                }
            tf = r.get("timeframe_days")
            if tf:
                matrix[key]["tfs"][tf] = float(r.get("slope_pct_per_day", 0) or 0) * 100

        # Filter options
        filter_source = st.multiselect(
            "Filter by source",
            sorted({m["source"] for m in matrix.values()}),
            default=[],
        )

        filtered_matrix = {
            k: v for k, v in matrix.items()
            if not filter_source or v["source"] in filter_source
        }

        # Build table
        mdata = []
        for _, m in filtered_matrix.items():
            row = {
                "Product": m["title"],
                "🏪 Source": m["source"],
                "Status": m["status"].upper(),
            }
            for tf in tf_order:
                tf_label = tf_labels.get(tf, f"{tf}d")
                slope = m["tfs"].get(tf)
                row[tf_label] = f"{slope:+.2f}%" if slope is not None else "—"
            row["🔗"] = m.get("url", "")
            row["💰 Price"] = f"${float(m['price']):.2f}" if m["price"] else "—"
            mdata.append(row)

        st.caption(f"Showing {len(mdata)} products")
        if mdata:
            st.dataframe(
                pd.DataFrame(mdata),
                width="stretch",
                hide_index=True,
                height=min(36 * len(mdata) + 36, 600),
                column_config={
                    "🔗": st.column_config.LinkColumn("🔗 Link", display_text="🔗"),
                },
            )

# ═══════════════════════════════════════════════════════════════════════════════
# 🔍 PRODUCT DEEP DIVE
# ═══════════════════════════════════════════════════════════════════════════════
elif view_mode == "🔍 Product Deep Dive":
    st.subheader("🔍 Product Deep Dive")
    st.caption("Select any product to see its full history, price chart, and pattern analysis.")

    rows = _load_all_classified()
    if not rows:
        st.info("No classified products yet.")
    else:
        # Build product list
        products_seen = set()
        product_list = []
        for r in rows:
            key = (r["source"], r["external_id"])
            if key not in products_seen:
                products_seen.add(key)
                product_list.append({
                    "source": r["source"],
                    "external_id": r["external_id"],
                    "title": (r["title"] or r["external_id"])[:60],
                    "price": r.get("price"),
                    "url": r.get("url") or "",
                })

        selected = st.selectbox(
            "Choose a product",
            [f"{p['title']} ({p['source']})" for p in product_list[:100]],
            key="deep_dive_select",
        )

        if selected:
            idx = [f"{p['title']} ({p['source']})" for p in product_list[:100]].index(selected)
            product = product_list[idx]

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("🏪 Source", product["source"])
            with col2:
                st.metric("💰 Price", f"${float(product['price']):.2f}" if product.get("price") else "—")
            with col3:
                url = product.get("url", "")
                if url:
                    st.markdown(f"🔗 **[Open Store Page →]({url})**")
                st.metric("🆔 ID", product["external_id"][:12])

            # Price chart
            st.subheader("📈 Price History")
            pts = _load_product_history(product["source"], product["external_id"])
            if pts and len(pts) > 1:
                df_pts = pd.DataFrame(pts)
                df_pts["captured_at"] = pd.to_datetime(df_pts["captured_at"])
                df_pts = df_pts.drop_duplicates("captured_at").set_index("captured_at")
                st.line_chart(df_pts["price"], height=350, width="stretch")

                # Stats
                prices = df_pts["price"].values
                stat_cols = st.columns(4)
                stat_cols[0].metric("First Price", f"${prices[0]:.2f}")
                stat_cols[1].metric("Latest Price", f"${prices[-1]:.2f}")
                stat_cols[2].metric("Change", f"{(prices[-1] - prices[0]) / prices[0] * 100:+.2f}%")
                stat_cols[3].metric("Obs Points", len(prices))
            else:
                st.info("⏳ Price history building up...")

            # Timeframe breakdown
            st.subheader("⏱️ Timeframe Status")
            try:
                tf_rows = storage.query(
                    "SELECT timeframe_days, status, slope_pct_per_day, n_points "
                    "FROM agg_product_status "
                    "WHERE source=? AND external_id=? "
                    "ORDER BY timeframe_days",
                    (product["source"], product["external_id"]),
                )
                if tf_rows:
                    tf_cols = st.columns(len(tf_rows))
                    for i, tr in enumerate(tf_rows):
                        slope = float(tr.get("slope_pct_per_day", 0) or 0) * 100
                        tf_cols[i].metric(
                            f"{tr['timeframe_days']}d",
                            f"{slope:+.2f}%",
                            tr["status"].upper(),
                        )
                else:
                    st.info("No timeframe data for this product yet.")
            except Exception:
                st.info("Timeframe data loading...")

# ═══════════════════════════════════════════════════════════════════════════════
# 📊 SENTIMENT DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
elif view_mode == "📊 Sentiment Dashboard":
    st.subheader("📊 Market Sentiment Dashboard")
    st.caption("Aggregate signals — what the market is doing across all products and timeframes.")

    sentiment = _load_sentiment_summary()

    # Status distribution
    status_counts = sentiment.get("status_counts", {})
    if status_counts:
        st.subheader("📊 Status Distribution")
        stat_cols = st.columns(4)
        for col, label, key in [
            (0, "🟢 Rising", "rising"),
            (1, "🔴 Declining", "declining"),
            (2, "🔵 Stable", "stable"),
            (3, "⚪ Unknown", "unknown"),
        ]:
            stat_cols[col].metric(label, status_counts.get(key, 0))

    # Pattern distribution
    patterns = sentiment.get("patterns", {})
    if patterns:
        st.subheader("🔍 Pattern Distribution")
        pcols = st.columns(min(4, len(patterns)))
        for i, (pattern, count) in enumerate(patterns.items()):
            icon = {"breakout": "🚀", "accelerating": "⚡", "rising": "📈",
                    "steady": "🔵", "declining": "📉", "unknown": "❓"}.get(pattern, "❓")
            pcols[i % 4].metric(f"{icon} {pattern.title()}", count)

    # Timeframe × Status heatmap
    tf_status = sentiment.get("tf_status", [])
    if tf_status:
        st.subheader("⏱️ Status by Timeframe")
        tf_data = {}
        for r in tf_status:
            tf = r.get("timeframe_days", 0)
            status = r.get("status", "unknown")
            n = r.get("n", 0)
            if tf not in tf_data:
                tf_data[tf] = {}
            tf_data[tf][status] = n

        if tf_data:
            tf_df = pd.DataFrame(tf_data).fillna(0).astype(int)
            st.bar_chart(tf_df.T, height=300)

    if not status_counts and not patterns:
        st.info("Sentiment data building up after the first few scan cycles.")

# ── TIMESTAMP ─────────────────────────────────────────────────────────────────
st.caption(f"\nUpdated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
