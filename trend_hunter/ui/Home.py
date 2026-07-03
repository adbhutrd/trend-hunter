"""trend-hunter — Streamlit landing page.

Run via:
    make dashboard
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.core.logging import configure

st.set_page_config(
    page_title="trend-hunter",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _boot() -> DuckDBStorage:
    s = get_settings()
    configure(s.log_dir, s.log_level)
    return DuckDBStorage(s.db_path, read_only=True)


@st.cache_resource(show_spinner=False)
def _get_storage() -> DuckDBStorage:
    return _boot()


# ── header ───────────────────────────────────────────────────────────────────
st.title("🛰️ trend-hunter")
st.caption(
    "Self-hosted Shopify trend-finder, B2B lead-generator, arbitrage engine.  "
    f"DB: `{get_settings().db_path}`  ·  {datetime.now(UTC):%Y-%m-%d %H:%M UTC}",
)

st.info(
    "👉 Open **🏠 Health** from the sidebar first. ",
    icon="ℹ️",
)

# ── quick health snapshot ────────────────────────────────────────────────────
storage = _get_storage()
try:
    rows = storage.query(
        """
        SELECT source, state, last_run, rows_in, error_rate, detail
        FROM health
        ORDER BY source
        """,
    )
except Exception as e:  # noqa: BLE001
    st.error(f"DB unreachable: {e}. Run `make init && make scan` first.")
    st.stop()

if not rows:
    st.warning(
        "No sources have ever written to the health table. Run `make scan` and reload this page.",
    )
    st.stop()

# ── kpi cards ────────────────────────────────────────────────────────────────
cols = st.columns(min(4, len(rows)))
for col, row in zip(cols, rows, strict=False):
    icon = {"ok": "✅", "stale": "⚠️", "failing": "❌", "dead": "💀"}.get(
        row["state"],
        "❓",
    )
    col.metric(
        label=f"{icon} {row['source']}",
        value=row["state"].upper(),
        delta=f"{row['rows_in']} rows",
        delta_color="off",
    )

st.divider()

# ── quick links ──────────────────────────────────────────────────────────────
st.subheader("Pages")
st.markdown(
    """
- 🏠 **Health** — heartbeat per source, last run, error rate
- 📈 **Trends** — top products classified as rising / declining / stable
- 🎯 **Leads** *(Phase 2)*
- 💰 **Arbitrage** *(Phase 3)*
- 📺 **Ads** *(Phase 3)*
- 🛒 **Scaffold** *(Phase 3)*
- 📊 **Money** *(Phase 3)*
- ⚙️ **Settings** — edit `sources.json`, tweak intervals, run doctor
""",
)
st.caption(
    "Phase 2/3 pages are placeholders intentionally. The pipeline + dashboard "
    "are real; the offensive money features land in subsequent phases.",
)
