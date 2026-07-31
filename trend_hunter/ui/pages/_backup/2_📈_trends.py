"""📈 Trends — classified product status dashboard.

Simple, robust view of all tracked products and their trend status.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings

st.set_page_config(page_title="Trends · trend-hunter", page_icon="📈", layout="wide")
st.title("📈 Trends")
st.caption("All tracked products with their current trend status. Auto-refreshes every 60s.")

st_autorefresh(interval=60_000, key="trends_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


@st.cache_data(ttl=15, show_spinner="Loading products...")
def _load_products():
    """Load all classified products."""
    try:
        result = storage.query(
            """SELECT source, external_id, title, status, slope_pct_per_day,
                      n_points, window_start, window_end
               FROM agg_product_status
               ORDER BY n_points DESC"""
        )
        return result
    except Exception as e:
        st.error(f"DB query failed: {e}")
        return []


rows = _load_products()

if not rows:
    st.warning(
        "No products tracked yet. The backend scheduler scans automatically "
        "every few minutes — data will appear as it builds up."
    )
    st.stop()


# Sidebar filters
st.sidebar.header("Filters")
all_sources = sorted({r["source"] for r in rows})
sel_sources = st.sidebar.multiselect("Source", all_sources, default=all_sources)

all_statuses = sorted({r["status"] for r in rows})
sel_status = st.sidebar.multiselect("Status", all_statuses, default=all_statuses)

min_points = st.sidebar.slider("Min observations", 3, 20, 3)

filtered = [
    r for r in rows
    if r["source"] in sel_sources
    and r["status"] in sel_status
    and (r["n_points"] or 0) >= min_points
]

# KPI cards
total = len(rows)
rising = sum(1 for r in rows if r["status"] == "rising")
declining = sum(1 for r in rows if r["status"] == "declining")
stable = sum(1 for r in rows if r["status"] == "stable")
unknown = sum(1 for r in rows if r["status"] == "unknown")

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("📦 Total", total)
k2.metric("🟢 Rising", rising)
k3.metric("🔴 Falling", declining)
k4.metric("⚪ Stable", stable)
k5.metric("❓ Unknown", unknown)

st.caption(
    f"Showing {len(filtered)} of {total} products. "
    "Price history builds over time — patterns emerge as the scheduler runs."
)

# Data table
if filtered:
    df = pd.DataFrame(filtered)
    if "slope_pct_per_day" in df.columns:
        df["change_%"] = (df["slope_pct_per_day"].fillna(0.0) * 100).round(3)

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_side_bar()
    gb.configure_default_column(filter=True, sortable=True, resizable=True)
    gb.configure_pagination(paginationAutoPageSize=False)

    AgGrid(
        df,
        gridOptions=gb.build(),
        theme="streamlit",
        height=500,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True,
    )
else:
    st.info("No products match your filters. Try adjusting the sidebar.")

# Price chart
st.divider()
st.subheader("📈 Price History")
picks = [f"{r['source']}/{r['external_id']}" for r in filtered[:100]]
if picks:
    picked = st.selectbox("Select a product to view price history", picks, key="price_chart")
    if picked:
        src, _, ext = picked.partition("/")
        pts = storage.query(
            "SELECT captured_at, price FROM products WHERE source=? AND external_id=? AND price IS NOT NULL ORDER BY captured_at",
            (src, ext),
        )
        if pts:
            df_pts = pd.DataFrame(pts)
            df_pts["captured_at"] = pd.to_datetime(df_pts["captured_at"])
            df_pts = df_pts.drop_duplicates("captured_at").set_index("captured_at")
            st.line_chart(df_pts, height=300, use_container_width=True)
            prices = df_pts["price"].dropna()
            if len(prices) >= 2:
                first = float(prices.iloc[0])
                last = float(prices.iloc[-1])
                chg = ((last - first) / first * 100) if first != 0 else 0.0
                st.caption(
                    f"First: ${float(prices.iloc[0]):.2f} → Current: ${float(prices.iloc[-1]):.2f} "
                    f"({chg:+.2f}%) · {len(prices)} observations"
                )
            else:
                st.caption(f"Price: ${float(prices.iloc[0]):.2f} · {len(prices)} observation(s)")
        else:
            st.info("No price data yet for this product.")
