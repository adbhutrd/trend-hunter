"""📈 Trends — classifier-driven product status dashboard."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings


st.set_page_config(page_title="Trends · trend-hunter", page_icon="📈", layout="wide")
st.title("📈 Trends")
st.caption(
    "Classified status per product (rising / declining / stable / unknown). "
    "Auto-refreshes every 60s. Run `make aggregate` to repopulate.",
)

st_autorefresh(interval=60_000, key="trends_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


@st.cache_data(ttl=15, show_spinner=False)
def _classified():
    try:
        rows = storage.query(
            """
            SELECT source, external_id, title, status,
                   slope_pct_per_day, n_points, window_start, window_end
            FROM agg_product_status
            ORDER BY status = 'unknown' ASC,
                     CASE status WHEN 'rising' THEN 0 WHEN 'declining' THEN 2
                                 WHEN 'stable' THEN 1 ELSE 3 END,
                     n_points DESC
            """,
        )
    except Exception:
        # before classify_all has run, table may not exist or be empty
        return []
    return rows


@st.cache_data(ttl=30, show_spinner=False)
def _status_counts():
    try:
        return storage.query(
            """
            SELECT status, count(*) AS n
            FROM agg_product_status
            GROUP BY status
            ORDER BY n DESC
            """,
        )
    except Exception:
        return []


rows = _classified()
counts = _status_counts()

if not rows:
    st.warning(
        "No classified products yet. Run `make run` (or `make scan && "
        "make aggregate`) from your terminal, then reload.",
    )
    st.stop()


# ── sidebar filters ──────────────────────────────────────────────────────────
statuses = sorted({r["status"] for r in rows})
sel = st.sidebar.multiselect(
    "Status",
    options=statuses,
    default=["rising", "stable", "declining"],
)

min_points = st.sidebar.slider(
    "Min observations per product",
    min_value=3,
    max_value=20,
    value=3,
)

filtered = [
    r for r in rows
    if r["status"] in sel and (r["n_points"] or 0) >= min_points
]


# ── KPI cards ────────────────────────────────────────────────────────────────
if counts:
    css_cols = st.columns(len(counts))
    for col, c in zip(css_cols, counts):
        col.metric(label=c["status"].upper(), value=c["n"])


# ── AG Grid table ────────────────────────────────────────────────────────────
_df = pd.DataFrame(filtered)
gb = GridOptionsBuilder.from_dataframe(_df)                                 # type: ignore[arg-type]
gb.configure_side_bar()
gb.configure_default_column(filter=True, sortable=True, resizable=True)
gb.configure_pagination(paginationAutoPageSize=False)

st.subheader(f"{len(filtered)} of {len(rows)} products classified")

AgGrid(
    _df,
    gridOptions=gb.build(),
    theme="streamlit",
    height=540,
    allow_unsafe_jscode=True,
    fit_columns_on_grid_load=True,
)


# ── raw points chart per product ─────────────────────────────────────────────
if filtered:
    st.subheader("Daily price series per filtered product")
    skus = [f"{r['source']}/{r['external_id']}" for r in filtered[:50]]
    picked = st.selectbox("Inspect product", options=skus)
    if picked:
        src, _, ext = picked.partition("/")
        try:
            pts = storage.query(
                """
                SELECT captured_at, price
                FROM products
                WHERE source = ? AND external_id = ?
                  AND price IS NOT NULL
                ORDER BY captured_at
                """,
                (src, ext),
            )
        except Exception:
            pts = []
        if pts:
            df = pd.DataFrame(pts)
            df["captured_at"] = pd.to_datetime(df["captured_at"])
            df = df.set_index("captured_at")
            st.line_chart(df, height=300)
        else:
            st.info("No price data for that SKU yet.")
