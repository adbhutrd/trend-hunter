"""🏠 Health — heartbeat per source, last run, error rate, ingestion volume."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings

st.set_page_config(page_title="Health · trend-hunter", page_icon="🏠", layout="wide")
st.title("🏠 Health")
st.caption("Per-source heartbeat — refreshes every 30s. Editable schedule in ⚙️ Settings.")

# Live auto-refresh (every 30s)
st_autorefresh(interval=30_000, key="health_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


@st.cache_data(ttl=15, show_spinner=False)
def _health_rows():
    return storage.query(
        "SELECT source, state, last_run, last_ok, rows_in, error_rate, detail "
        "FROM health ORDER BY source",
    )


@st.cache_data(ttl=15, show_spinner=False)
def _volume_per_source(days: int):
    return storage.query(
        f"""
        SELECT source, date_trunc('day', captured_at) AS day, count(*) AS rows
        FROM products
        WHERE captured_at >= now() - INTERVAL {int(days)} DAY
        GROUP BY source, day
        ORDER BY day DESC, source
        """,
    )


try:
    rows = _health_rows()
except Exception as e:                                                        # noqa: BLE001
    st.error(f"DB unreachable: {e}")
    st.stop()


if not rows:
    st.warning("Health table empty. Run `make scan` from your terminal.")
    st.stop()


# ── AG Grid for sort/filter/export ───────────────────────────────────────────
_df = pd.DataFrame(rows)
gb = GridOptionsBuilder.from_dataframe(_df)  # type: ignore[arg-type]
gb.configure_side_bar()
gb.configure_default_column(filter=True, sortable=True, resizable=True)
gb.configure_pagination(paginationAutoPageSize=False)


st.subheader(f"Per-source heartbeat — at {datetime.now(UTC):%H:%M:%S} UTC")
AgGrid(
    _df,
    gridOptions=gb.build(),
    theme="streamlit",
    height=300,
    allow_unsafe_jscode=True,
    fit_columns_on_grid_load=True,
)


# ── ingestion volume chart (last N days) ─────────────────────────────────────
st.subheader("Daily row volume per source")
days = st.slider("Window (days)", min_value=1, max_value=30, value=14)
vol = _volume_per_source(days)
if vol:
    import pandas as pd
    df = pd.DataFrame(vol)
    df["day"] = pd.to_datetime(df["day"])
    pivot = df.pivot_table(index="day", columns="source", values="rows", fill_value=0)
    st.bar_chart(pivot, height=320)
else:
    st.info("No data in selected window yet.")


# ── control panel ────────────────────────────────────────────────────────────
st.subheader("Controls")
col1, col2, col3 = st.columns(3)
with col1:
    if st.button("🔁 Reload now", help="clear caches and re-fetch"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()
with col2:
    st.code(
        "make scan     # one-shot ingest\n"
        "make doctor   # self-test\n"
        "make aggregate",
        language="bash",
    )
with col3:
    st.caption(
        "Trend-hunter runs `python -m trend_hunter.cli <cmd>` from your terminal. "
        "The dashboard only reads — scraping never blocks the UI.",
    )
