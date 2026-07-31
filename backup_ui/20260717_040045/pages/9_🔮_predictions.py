"""🔮 ML Predictions — AI-powered trend forecasting using Prophet.

This page shows:
  - 📊 Forecast Overview — how many products have ML predictions
  - 🚀 Predicted Breakouts — products predicted to rise >2%/day
  - 📈 Rising Predicted — products predicted to rise >0.5%/day
  - 📉 Declining Predicted — products predicted to fall
  - 🎯 Per-Product Deep Dive — select a product to see its forecast chart
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import apply_professional_theme

st.set_page_config(page_title="ML Predictions · trend-hunter", page_icon="🔮", layout="wide")
apply_professional_theme()
st.title("🔮 ML Predictions")
st.caption("Prophet-powered AI forecasts — predicting trend direction 7/14/30 days ahead.")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


# ── DATA LOADERS ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner="Loading ML predictions...")
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
def _load_prediction_counts():
    try:
        return storage.query("""
            SELECT predicted_status, horizon_days, count(*) as n
            FROM ml_predictions
            GROUP BY predicted_status, horizon_days
            ORDER BY horizon_days, predicted_status
        """)
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_status_comparison():
    """Compare ML predictions against current classified status."""
    try:
        return storage.query("""
            SELECT m.source, m.external_id, m.title, m.predicted_status,
                   m.predicted_slope, m.confidence, m.horizon_days,
                   a.status as current_status
            FROM ml_predictions m
            LEFT JOIN agg_product_status a
                ON m.source = a.source AND m.external_id = a.external_id
            WHERE a.status IS NOT NULL
            ORDER BY m.horizon_days, m.confidence DESC
        """)
    except Exception:
        return []





# ── SECTION 1: OVERVIEW METRICS ──────────────────────────────────────────────
st.subheader("📊 Forecast Overview")

predictions = _load_predictions()
status_counts = _load_prediction_counts()

if not predictions:
    st.warning("No ML predictions yet. Run `make run` or wait for the scheduler to scan & aggregate data.")
    st.info("The ML forecaster needs at least 5 data points per product in trend_history. It runs automatically after each aggregation cycle.")
    st.stop()

# Metrics strip
total_preds = len(predictions)
unique_products = len({(r["source"], r["external_id"]) for r in predictions})

# Count by status
status_breakdown: dict[str, int] = {}
for r in predictions:
    s = r["predicted_status"]
    status_breakdown[s] = status_breakdown.get(s, 0) + 1

breakout_count = status_breakdown.get("breakout", 0)
rising_count = status_breakdown.get("rising", 0)
declining_count = status_breakdown.get("declining", 0)
stable_count = status_breakdown.get("stable", 0)

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("🔮 Total Predictions", f"{total_preds:,}")
m2.metric("📦 Products Modeled", f"{unique_products:,}")
m3.metric("🚀 Breakouts", f"{breakout_count:,}")
m4.metric("📈 Rising", f"{rising_count:,}")
m5.metric("📉 Declining", f"{declining_count:,}")
m6.metric("🔵 Stable", f"{stable_count:,}")

# Per-horizon breakdown
if status_counts:
    st.caption("Predictions by horizon and status:")
    hcols = st.columns(3)
    for i, horizon in enumerate([7, 14, 30]):
        h_rows = [r for r in status_counts if r["horizon_days"] == horizon]
        if h_rows:
            summary = ", ".join(
                f"{r['predicted_status']}: {r['n']}" for r in h_rows
            )
            hcols[i].metric(f"{horizon}-Day Forecast", summary.split(",")[0] if "," in summary else summary)
        else:
            hcols[i].metric(f"{horizon}-Day Forecast", "No data")

st.divider()

# ── SECTION 2: BREAKOUT & RISING TABLES ─────────────────────────────────────
tab_breakout, tab_rising, tab_declining, tab_all = st.tabs([
    "🚀 Breakouts", "📈 Rising", "📉 Declining", "📋 All Predictions"
])

with tab_breakout:
    st.caption("Products predicted to break out (>2% slope per day) — strongest buy signals.")
    breakout_preds = [r for r in predictions if r["predicted_status"] == "breakout"]
    if breakout_preds:
        data = []
        for r in breakout_preds[:20]:
            data.append({
                "Product": (r["title"] or r["external_id"])[:50],
                "📈 Predicted Slope": f"{float(r['predicted_slope'])*100:+.2f}%/day",
                f"{r['horizon_days']}d Conf": f"{float(r['confidence'])*100:.0f}%",
                "🎯 Horizon": f"{r['horizon_days']}d",
                "🏪 Source": r["source"],
            })
        st.dataframe(
            pd.DataFrame(data),
            use_container_width=True,
            hide_index=True,
            height=min(42 * len(data) + 42, 600),
        )
    else:
        st.info("No breakout predictions yet.")

with tab_rising:
    st.caption("Products predicted to rise (>0.5% but <2% slope per day).")
    rising_preds = [r for r in predictions if r["predicted_status"] == "rising"]
    if rising_preds:
        data = []
        for r in rising_preds[:20]:
            data.append({
                "Product": (r["title"] or r["external_id"])[:50],
                "📈 Slope": f"{float(r['predicted_slope'])*100:+.2f}%/day",
                "Confidence": f"{float(r['confidence'])*100:.0f}%",
                "🎯 Horizon": f"{r['horizon_days']}d",
                "🏪 Source": r["source"],
            })
        st.dataframe(
            pd.DataFrame(data),
            use_container_width=True,
            hide_index=True,
            height=min(42 * len(data) + 42, 600),
        )
    else:
        st.info("No rising predictions yet.")

with tab_declining:
    st.caption("Products predicted to decline (<-0.5% slope per day).")
    decl_preds = [r for r in predictions if r["predicted_status"] == "declining"]
    if decl_preds:
        data = []
        for r in decl_preds[:20]:
            data.append({
                "Product": (r["title"] or r["external_id"])[:50],
                "📉 Slope": f"{float(r['predicted_slope'])*100:+.2f}%/day",
                "Confidence": f"{float(r['confidence'])*100:.0f}%",
                "🎯 Horizon": f"{r['horizon_days']}d",
                "🏪 Source": r["source"],
            })
        st.dataframe(
            pd.DataFrame(data),
            use_container_width=True,
            hide_index=True,
            height=min(42 * len(data) + 42, 600),
        )
    else:
        st.info("No declining predictions — good sign!")

with tab_all:
    st.caption("All ML predictions sorted by absolute slope (highest momentum first).")
    data = []
    for r in predictions[:50]:
        status_icon = {
            "breakout": "🚀", "rising": "📈",
            "stable": "🔵", "declining": "📉"
        }.get(r["predicted_status"], "❓")
        data.append({
            "Product": (r["title"] or r["external_id"])[:45],
            f"{status_icon} Predicted": r["predicted_status"].upper(),
            "📊 Slope": f"{float(r['predicted_slope'])*100:+.2f}%/day",
            "🎯 Horizon": f"{r['horizon_days']}d",
            "🎲 Confidence": f"{float(r['confidence'])*100:.0f}%",
            "🏪 Source": r["source"],
        })
    st.dataframe(
        pd.DataFrame(data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(data) + 42, 700),
    )

st.divider()

# ── SECTION 3: CURRENT vs ML PREDICTED COMPARISON ────────────────────────────
st.subheader("📊 Current Status vs ML Prediction")
st.caption("How the ML forecast compares to the current classified status.")

comparison = _load_status_comparison()
if comparison:
    # Find mismatches: ML predicts different from current
    mismatches = [
        r for r in comparison
        if r["predicted_status"] != r["current_status"]
        and r["predicted_status"] in ("breakout", "rising")
    ]
    if mismatches:
        st.markdown("##### ⚠️ Products Where ML Disagrees (ML says bullish, current says not)")
        data = []
        for r in mismatches[:15]:
            data.append({
                "Product": (r["title"] or r["external_id"])[:45],
                "🔮 ML Says": r["predicted_status"].upper(),
                "📊 Current": (r["current_status"] or "?").upper(),
                "📈 ML Slope": f"{float(r['predicted_slope'])*100:+.2f}%/day",
                "🎯 Horizon": f"{r['horizon_days']}d",
                "🎲 Confidence": f"{float(r['confidence'])*100:.0f}%",
            })
        st.dataframe(
            pd.DataFrame(data),
            use_container_width=True,
            hide_index=True,
            height=min(42 * len(data) + 42, 500),
        )
    else:
        st.success("ML predictions are in alignment with current classifications. No disagreements found.")
else:
    st.info("No comparison data available yet.")

st.divider()

# ── TIMESTAMP ─────────────────────────────────────────────────────────────────
st.caption(f"Last updated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC · "
           f"Data source: `ml_predictions` + `trend_history` + `agg_product_status`")
