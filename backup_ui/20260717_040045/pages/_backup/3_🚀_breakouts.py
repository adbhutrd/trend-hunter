"""🚀 Breakouts — historical trend patterns and momentum highlights.

Reads from the immutable ``trend_history`` ledger written by
:class:`trend_hunter.intelligence.aggregator.classify_all`.  It surfaces
per-timeframe patterns (breakout, accelerating, cooling, etc.) and
cross-timeframe patterns so operators can spot momentum early.
"""

from __future__ import annotations

from datetime import UTC

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.intelligence.patterns import (
    Pattern,
    build_cross_timeframe_snapshots,
    detect_multi_timeframe_pattern,
    detect_pattern,
    group_snapshots_by_timeframe,
)

st.set_page_config(page_title="Breakouts · trend-hunter", page_icon="🚀", layout="wide")
st.title("🚀 Breakouts & Momentum")

st_autorefresh(interval=60_000, key="breakout_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


@st.cache_data(ttl=30, show_spinner=False)
def _load_history() -> list[dict]:
    try:
        return _storage().query(
            """
            SELECT source, external_id, title, status, slope_pct_per_day,
                   n_points, window_start, window_end, timeframe_days, recorded_at
            FROM trend_history
            ORDER BY source, external_id, timeframe_days, window_end ASC
            """,
        )
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_recent_alerts() -> list[dict]:
    """Read recent pattern-change alerts from cross_timeframe_patterns."""
    try:
        return _storage().query(
            """
            SELECT source, external_id, title, pattern, status,
                   slope_pct_per_day, window_end, detected_at, alerted_at
            FROM cross_timeframe_patterns
            WHERE pattern IN ('breakout', 'accelerating')
            ORDER BY detected_at DESC
            """,
        )
    except Exception:
        return []


try:
    history = _load_history()
except Exception as e:  # noqa: BLE001
    st.error(f"DB unreachable: {e}. Run `make init && make run` first.")
    st.stop()

# Freshness context for the snapshot.
latest_ts = max((r["window_end"] for r in history), default=None)
if latest_ts is not None:
    latest_ts = pd.to_datetime(latest_ts)
    if latest_ts.tzinfo is None:
        latest_ts = latest_ts.replace(tzinfo=UTC)
ts_label = latest_ts.strftime("%Y-%m-%d %H:%M UTC") if latest_ts is not None else "none yet"
st.caption(
    "Historical trend pattern detection across daily, weekly, 2-week, "
    "monthly, and quarterly windows. Auto-refreshes every 60s. "
    f"Latest snapshot: {ts_label}."
)

if not history:
    st.warning(
        "No trend history found. Run `make run` (or `make aggregate`) "
        "from your terminal to populate `trend_history`, then reload.",
    )
    st.stop()

# ── compute patterns ─────────────────────────────────────────────────────────
grouped_tf = group_snapshots_by_timeframe(history)
tf_patterns: list[dict] = []
for (src, ext, tf), snaps in grouped_tf.items():
    pat = detect_pattern(snaps)
    if pat:
        latest = snaps[-1]
        tf_patterns.append(
            {
                "source": src,
                "external_id": ext,
                "title": latest.get("title") or f"{src}/{ext}",
                "timeframe_days": tf,
                "pattern": str(pat),
                "status": latest.get("status"),
                "slope_pct_per_day": latest.get("slope_pct_per_day"),
                "n_points": latest.get("n_points"),
                "window_end": latest.get("window_end"),
            }
        )

cross_groups = build_cross_timeframe_snapshots(history)
mtf_patterns: list[dict] = []
for snaps in cross_groups:
    mtf_pat = detect_multi_timeframe_pattern(snaps)
    if mtf_pat:
        latest = snaps[-1]
        mtf_patterns.append(
            {
                "source": latest["source"],
                "external_id": latest["external_id"],
                "title": latest.get("title") or f"{latest['source']}/{latest['external_id']}",
                "cross_pattern": str(mtf_pat),
                "short_tf_slope": latest.get("slope_pct_per_day"),
            }
        )

# ── sidebar filters ────────────────────────────────────────────────────────────
if not tf_patterns:
    st.info(
        "Trend history exists, but no patterns have been detected yet. "
        "Run `make run` a few more times to build up history, then reload.",
    )
    st.stop()

all_sources = sorted({r["source"] for r in tf_patterns})
sel_sources = st.sidebar.multiselect("Source", options=all_sources, default=all_sources)

all_tfs = sorted({r["timeframe_days"] for r in tf_patterns})
sel_tf = st.sidebar.multiselect("Timeframe (days)", options=all_tfs, default=all_tfs)

all_pats = sorted({r["pattern"] for r in tf_patterns})
highlight_defaults = [
    Pattern.BREAKOUT.value,
    Pattern.ACCELERATING.value,
    Pattern.REBOUNDING.value,
]
default_pats = [p for p in all_pats if p in highlight_defaults] or all_pats
sel_pat = st.sidebar.multiselect("Pattern", options=all_pats, default=default_pats)

min_points = st.sidebar.slider(
    "Min observations per snapshot",
    min_value=3,
    max_value=20,
    value=3,
)

filtered_tf = [
    r
    for r in tf_patterns
    if r["source"] in sel_sources
    and r["timeframe_days"] in sel_tf
    and r["pattern"] in sel_pat
    and (r["n_points"] or 0) >= min_points
]

# ── 🔔 Recent Pattern-Change Alerts ────────────────────────────────────────────
recent_alerts = _load_recent_alerts()
if recent_alerts:
    st.subheader("🔔 Recent Pattern-Change Alerts")
    st.caption(
        "Products whose cross-timeframe pattern recently changed to "
        "breakout or accelerating. Detected automatically by the backend "
        "scheduler — no external messaging needed."
    )
    alert_cols = st.columns(min(3, len(recent_alerts)))
    for idx, row in enumerate(recent_alerts[:6]):
        with alert_cols[idx % 3].container(border=True):
            is_new = row.get("alerted_at") is not None
            emoji = "🆕" if is_new else "🔥"
            st.markdown(f"{emoji} **{row['title'][:40]}**")
            st.caption(f"`{row['source']}/{row['external_id']}`")
            slope = float(row.get("slope_pct_per_day") or 0.0) * 100
            detected = row.get("detected_at")
            if detected:
                st.caption(f"Detected: {detected.strftime('%Y-%m-%d %H:%M')}")
            st.metric(
                label="Pattern",
                value=str(row["pattern"]).title(),
                delta=f"{slope:+.2f}% slope",
                delta_color="normal",
            )
    if len(recent_alerts) > 6:
        st.caption(f"...and {len(recent_alerts) - 6} more recent alerts below")
    st.divider()

# ── KPI cards ─────────────────────────────────────────────────────────────────
st.subheader("Momentum Signals")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Products Tracked", len({(r["source"], r["external_id"]) for r in history}))
k2.metric(
    "Breakouts",
    sum(1 for r in tf_patterns if r["pattern"] == Pattern.BREAKOUT.value),
)
k3.metric(
    "Accelerating",
    sum(1 for r in tf_patterns if r["pattern"] == Pattern.ACCELERATING.value),
)
k4.metric(
    "Cross-TF Breakouts/Accel",
    sum(
        1
        for r in mtf_patterns
        if r["cross_pattern"]
        in (Pattern.BREAKOUT.value, Pattern.ACCELERATING.value)
    ),
)

st.divider()

# ── cross-timeframe highlights ────────────────────────────────────────────────
st.subheader("🎯 Cross-Timeframe Highlights")
highlights = [
    r
    for r in mtf_patterns
    if r["cross_pattern"]
    in (Pattern.BREAKOUT.value, Pattern.ACCELERATING.value, Pattern.REBOUNDING.value)
]

if highlights:
    n_cols = min(3, len(highlights))
    h_cols = st.columns(n_cols)
    for col, h in zip(h_cols, highlights[:n_cols], strict=False):
        with col.container(border=True):
            st.markdown(f"**{h['title'][:40]}**")
            st.caption(f"`{h['source']}/{h['external_id']}`")
            slope = float(h["short_tf_slope"] or 0.0) * 100
            st.metric(
                label="Pattern",
                value=h["cross_pattern"].title(),
                delta=f"{slope:+.2f}% slope",
                delta_color="normal",
            )
    if len(highlights) > n_cols:
        st.caption(f"...and {len(highlights) - n_cols} more cross-timeframe signals below")
else:
    st.info("No strong cross-timeframe breakouts or accelerations detected yet.")

st.divider()

# ── cross-timeframe signals table ─────────────────────────────────────────────
st.subheader("Cross-Timeframe Signals")
if mtf_patterns:
    mtf_df = pd.DataFrame(mtf_patterns)
    mtf_df["short_tf_slope"] = mtf_df["short_tf_slope"].fillna(0.0).round(4)

    gb_mtf = GridOptionsBuilder.from_dataframe(mtf_df)  # type: ignore[arg-type]
    gb_mtf.configure_side_bar()
    gb_mtf.configure_default_column(filter=True, sortable=True, resizable=True)
    gb_mtf.configure_pagination(paginationAutoPageSize=False)

    AgGrid(
        mtf_df,
        gridOptions=gb_mtf.build(),
        theme="streamlit",
        height=300,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True,
    )
else:
    st.info("No cross-timeframe patterns detected yet.")

st.divider()

# ── per-timeframe pattern table ───────────────────────────────────────────────
st.subheader("Per-Timeframe Patterns")
if filtered_tf:
    df = pd.DataFrame(filtered_tf)
    df["slope_pct_per_day"] = df["slope_pct_per_day"].round(4)

    gb = GridOptionsBuilder.from_dataframe(df)  # type: ignore[arg-type]
    gb.configure_side_bar()
    gb.configure_default_column(filter=True, sortable=True, resizable=True)
    gb.configure_pagination(paginationAutoPageSize=False)

    AgGrid(
        df,
        gridOptions=gb.build(),
        theme="streamlit",
        height=540,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True,
    )
else:
    st.info("No patterns match your filters.")

st.divider()

# ── pattern distribution chart ───────────────────────────────────────────────
st.subheader("Pattern Distribution")
if tf_patterns:
    dist_df = (
        pd.DataFrame(tf_patterns)
        .groupby("pattern")
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    st.bar_chart(dist_df.set_index("pattern"), height=300)
else:
    st.info("No patterns to chart yet.")
