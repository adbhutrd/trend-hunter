"""🔮 AI Forecasts — What the AI predicts will rise or fall. Simple predictions."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import apply_professional_theme

st.set_page_config(page_title="AI Forecasts · trend-hunter", page_icon="🔮", layout="wide")
apply_professional_theme()
st.title("🔮 AI Product Forecasts")
st.caption(
    "The AI analyzes product price history and predicts where each product is heading "
    "over the next 7, 14, and 30 days."
)


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


@st.cache_data(ttl=30, show_spinner="Loading AI forecasts...")
def _load_predictions():
    try:
        return storage.query("""
            SELECT source, external_id, title, horizon_days,
                   predicted_slope, predicted_status, confidence,
                   lower_bound, upper_bound, forecasted_at
            FROM ml_predictions
            ORDER BY ABS(predicted_slope) DESC, confidence DESC
        """)
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_counts():
    try:
        return storage.query("""
            SELECT predicted_status, horizon_days, count(*) as n
            FROM ml_predictions
            GROUP BY predicted_status, horizon_days
            ORDER BY horizon_days, predicted_status
        """)
    except Exception:
        return []


predictions = _load_predictions()
status_counts = _load_counts()

if not predictions:
    st.warning("⏳ AI forecasts are being generated. Check back after the next scan cycle.")
    st.info("The AI needs at least 5 data points per product to make a prediction. "
            "This happens automatically after each scan.")
    st.stop()

# ── OVERVIEW CARDS ────────────────────────────────────────────────────────────
st.markdown("### 📊 Forecast Summary")

total_preds = len(predictions)
unique_prods = len({(r["source"], r["external_id"]) for r in predictions})

status_brk = {}
for r in predictions:
    s = r["predicted_status"]
    status_brk[s] = status_brk.get(s, 0) + 1

c1, c2, c3, c4 = st.columns(4)
c1.metric("🔮 **Total Forecasts**", f"{total_preds:,}", help=f"Across {unique_prods} products")
c2.metric("📈 **Predicted Rising**", status_brk.get("rising", 0), help="AI says these will go up")
c3.metric("📉 **Predicted Declining**", status_brk.get("declining", 0), help="AI says these will go down")
c4.metric("🔵 **Predicted Stable**", status_brk.get("stable", 0), help="AI says these will hold steady")

if status_counts:
    st.caption("Breakdown by forecast window:")
    hcols = st.columns(3)
    for i, horizon in enumerate([7, 14, 30]):
        hrows = [r for r in status_counts if r["horizon_days"] == horizon]
        if hrows:
            parts = [f"{r['predicted_status']}: {r['n']}" for r in hrows]
            hcols[i].metric(f"**{horizon}-Day Forecast**", " · ".join(parts))
        else:
            hcols[i].metric(f"**{horizon}-Day Forecast**", "No data")

st.divider()

# ── PREDICTIONS BY TYPE ──────────────────────────────────────────────────────
st.markdown("### 🔮 Products to Watch")

tab_rising, tab_declining, tab_all = st.tabs(["📈 Predicted to Rise", "📉 Predicted to Decline", "📋 All Forecasts"])

with tab_rising:
    st.caption("Products the AI predicts will go up in the coming days.")
    rising = [r for r in predictions if r["predicted_status"] in ("rising", "breakout")]
    if rising:
        data = []
        for r in rising[:20]:
            data.append({
                "Product": (r["title"] or r["external_id"])[:50],
                "📈 Predicted": f"+{float(r['predicted_slope'])*100:.1f}%",
                "🎯 Horizon": f"{r['horizon_days']} days",
                "🎲 Confidence": f"{float(r['confidence'])*100:.1f}%",
                "🏪 Store": r["source"].title(),
            })
        st.dataframe(pd.DataFrame(data), width="stretch", hide_index=True,
                     height=min(42 * len(data) + 42, 600))
    else:
        st.info("No rising predictions yet — the AI is still learning from data.")

with tab_declining:
    st.caption("Products the AI predicts will go down in the coming days.")
    declining = [r for r in predictions if r["predicted_status"] == "declining"]
    if declining:
        data = []
        for r in declining[:20]:
            data.append({
                "Product": (r["title"] or r["external_id"])[:50],
                "📉 Predicted": f"{float(r['predicted_slope'])*100:.1f}%",
                "🎯 Horizon": f"{r['horizon_days']} days",
                "🎲 Confidence": f"{float(r['confidence'])*100:.1f}%",
                "🏪 Store": r["source"].title(),
            })
        st.dataframe(pd.DataFrame(data), width="stretch", hide_index=True,
                     height=min(42 * len(data) + 42, 600))
    else:
        st.info("No declining predictions — good sign!")

with tab_all:
    st.caption("All AI forecasts sorted by strongest signal first.")
    data = []
    for r in predictions[:50]:
        icon = {"breakout": "🚀", "rising": "📈", "stable": "🔵", "declining": "📉"}.get(
            r["predicted_status"], "❓"
        )
        data.append({
            f"{icon} Signal": r["predicted_status"].upper(),
            "Product": (r["title"] or r["external_id"])[:45],
            "📊 Strength": f"{float(r['predicted_slope'])*100:+.1f}%",
            "🎯 Horizon": f"{r['horizon_days']}d",
            "🎲 Confidence": f"{float(r['confidence'])*100:.0f}%",
            "🏪 Store": r["source"].title(),
        })
    st.dataframe(pd.DataFrame(data), width="stretch", hide_index=True,
                 height=min(42 * len(data) + 42, 700))

st.divider()

# ── HOW IT WORKS ──────────────────────────────────────────────────────────────
st.markdown("### 🤖 How the AI Makes Predictions")
with st.expander("Click to learn more"):
    st.markdown("""
    - **What it uses:** The AI analyzes each product's recent price history
    - **How it works:** It fits a trend line and projects it forward — like an arrow pointing the direction prices are heading
    - **Timeframes:** Predicts 7 days, 14 days, and 30 days ahead
    - **Confidence:** Higher confidence when data is consistent and shows a clear trend
    - **Accuracy gets better over time** as more data is collected

    *Think of it like a weather forecast for product prices — not perfect, but directionally useful.*
    """)

st.caption(f"Last updated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
