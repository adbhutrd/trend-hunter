"""📡 LIVE MONITOR — Real-time alerts, daily/weekly reports, and pattern change tracking.

The surveillance center:
  - 🔔 Real-Time Alerts — product pattern changes as they happen
  - 📊 Daily Trend Report — biggest movers today
  - 📈 Weekly Trend Report — biggest movers this week
  - 📋 All Pattern Changes — every status transition across products
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import apply_professional_theme

st.set_page_config(page_title="Live Monitor · trend-hunter", page_icon="📡", layout="wide")
apply_professional_theme()
st.title("📡 Live Monitor")
st.caption("Real-time surveillance — detects every pattern change and trend shift as data flows in.")

st_autorefresh(interval=30_000, key="monitor_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


# ── DATA LOADERS ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=20, show_spinner="Loading alerts...")
def _load_alerts():
    """Load cross-timeframe pattern alerts, exclude steady/unknown."""
    try:
        return storage.query(
            "SELECT source, external_id, title, pattern, status, "
            "slope_pct_per_day, window_end, detected_at, alerted_at "
            "FROM cross_timeframe_patterns "
            "WHERE pattern NOT IN ('steady', 'unknown') "
            "ORDER BY detected_at DESC LIMIT 30"
        )
    except Exception:
        return []


@st.cache_data(ttl=20, show_spinner="Building daily report...")
def _load_daily_report():
    """Products with strongest movement in the last 24h."""
    try:
        return storage.query(
            """
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
            WHERE a.timeframe_days IN (1, 7)
            AND a.n_points >= 2
            ORDER BY ABS(a.slope_pct_per_day) DESC
            LIMIT 30
        """
        )
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_weekly_report():
    """Products with strongest movement in 14-30 day windows."""
    try:
        return storage.query(
            """
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
            WHERE a.timeframe_days IN (14, 30, 90)
            AND a.n_points >= 3
            ORDER BY ABS(a.slope_pct_per_day) DESC
            LIMIT 30
        """
        )
    except Exception:
        return []


@st.cache_data(ttl=20, show_spinner=False)
def _load_all_patterns():
    """All cross-timeframe patterns for aggregate view."""
    try:
        return storage.query(
            "SELECT pattern, count(*) as n "
            "FROM cross_timeframe_patterns GROUP BY pattern ORDER BY n DESC"
        )
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_status_transitions():
    """Check for status changes by comparing current vs previous in trend_history."""
    try:
        return storage.query(
            """
            SELECT source, external_id, title, status, window_end
            FROM trend_history
            WHERE window_end >= now() - INTERVAL '1' DAY
            ORDER BY window_end DESC
            LIMIT 50
        """
        )
    except Exception:
        return []


# ── SECTION 1: ALERT DASHBOARD ───────────────────────────────────────────────
st.subheader("🔔 Active Alerts")
st.caption("Products with non-steady patterns — breakout, accelerating, rising, or declining.")

alerts = _load_alerts()
pattern_counts = _load_all_patterns()

# Pattern count summary
if pattern_counts:
    pcols = st.columns(min(len(pattern_counts), 5))
    for i, pc in enumerate(pattern_counts):
        icon = {"breakout": "🚀", "accelerating": "⚡", "rising": "📈",
                "steady": "🔵", "declining": "📉", "unknown": "❓"}.get(pc["pattern"], "🔍")
        pcols[i].metric(f"{icon} {pc['pattern'].title()}", pc["n"])
else:
    st.info("No patterns detected yet — data builds after each scan cycle.")

st.divider()

if alerts:
    alert_data = []
    for a in alerts[:20]:
        slope = float(a.get("slope_pct_per_day", 0) or 0) * 100
        icon = {"breakout": "🚀", "accelerating": "⚡", "rising": "📈",
                "declining": "📉"}.get(a["pattern"], "🔍")

        detected = a.get("detected_at") or a.get("window_end")
        time_str = detected.strftime("%m/%d %H:%M") if hasattr(detected, "strftime") else "?"

        alert_data.append({
            f"{icon} Pattern": a["pattern"].upper(),
            "Product": (a["title"] or a["external_id"])[:40],
            "🏪 Source": a["source"],
            "📈 Slope": f"{slope:+.2f}%/day",
            "Status": a.get("status", "?").upper(),
            "🕐 Detected": time_str,
        })

    st.dataframe(
        pd.DataFrame(alert_data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(alert_data) + 42, 600),
    )
else:
    st.info("No active alerts. All products currently have steady/unknown patterns.")

st.divider()

# ── SECTION 2: 📊 DAILY TREND REPORT ─────────────────────────────────────────
st.subheader("📊 Daily Trend Report (Last 24h)")
st.caption("Biggest movers in 1-day and 7-day windows — updated every 30 seconds.")

daily = _load_daily_report()
if daily:
    # Group by product, pick best slope
    dmap = {}
    for r in daily:
        key = (r["source"], r["external_id"])
        if key not in dmap:
            dmap[key] = {
                "title": (r["title"] or r["external_id"])[:40],
                "source": r["source"],
                "price": r.get("price"),
                "url": r.get("url") or "",
                "status": r.get("status", "?"),
                "best_slope": 0.0,
                "best_tf": 0,
                "slopes": [],
            }
        slope = float(r.get("slope_pct_per_day", 0) or 0) * 100
        dmap[key]["slopes"].append(slope)
        if abs(slope) > abs(dmap[key]["best_slope"]):
            dmap[key]["best_slope"] = slope
            dmap[key]["best_tf"] = r.get("timeframe_days", 0)

    # Sort by absolute daily movement
    daily_sorted = sorted(dmap.values(), key=lambda x: abs(x["best_slope"]), reverse=True)

    d_data = []
    for i, dp in enumerate(daily_sorted[:15], 1):
        direction = "🟢" if dp["best_slope"] > 0 else "🔴"
        d_data.append({
            "#": i,
            "Product": dp["title"],
            direction: f"{dp['best_slope']:+.2f}%",
            "⏱️ Window": f"{dp['best_tf']}d",
            "🏪 Source": dp["source"],
            "Status": dp["status"].upper(),
            "💰 Price": f"${float(dp['price']):.2f}" if dp["price"] else "—",
            "🔗": dp.get("url", ""),
        })

    st.dataframe(
        pd.DataFrame(d_data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(d_data) + 42, 500),
        column_config={
            "🔗": st.column_config.LinkColumn("🔗 Link", display_text="🔗"),
        },
    )
else:
    st.info("Daily report data building up...")

st.divider()

# ── SECTION 3: 📈 WEEKLY TREND REPORT ────────────────────────────────────────
st.subheader("📈 Weekly Trend Report (14-90 Day Windows)")
st.caption("Biggest movers in medium-to-long-term windows — shows sustained trends.")

weekly = _load_weekly_report()
if weekly:
    wmap = {}
    for r in weekly:
        key = (r["source"], r["external_id"])
        if key not in wmap:
            wmap[key] = {
                "title": (r["title"] or r["external_id"])[:40],
                "source": r["source"],
                "price": r.get("price"),
                "url": r.get("url") or "",
                "status": r.get("status", "?"),
                "best_slope": 0.0,
                "best_tf": 0,
                "slopes": [],
            }
        slope = float(r.get("slope_pct_per_day", 0) or 0) * 100
        wmap[key]["slopes"].append(slope)
        if abs(slope) > abs(wmap[key]["best_slope"]):
            wmap[key]["best_slope"] = slope
            wmap[key]["best_tf"] = r.get("timeframe_days", 0)

    weekly_sorted = sorted(wmap.values(), key=lambda x: abs(x["best_slope"]), reverse=True)

    w_data = []
    for i, wp in enumerate(weekly_sorted[:15], 1):
        direction = "🟢" if wp["best_slope"] > 0 else "🔴"
        w_data.append({
            "#": i,
            "Product": wp["title"],
            direction: f"{wp['best_slope']:+.2f}%",
            "⏱️ Window": f"{wp['best_tf']}d",
            "🏪 Source": wp["source"],
            "Status": wp["status"].upper(),
            "💰 Price": f"${float(wp['price']):.2f}" if wp["price"] else "—",
            "🔗": wp.get("url", ""),
        })

    st.dataframe(
        pd.DataFrame(w_data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(w_data) + 42, 500),
        column_config={
            "🔗": st.column_config.LinkColumn("🔗 Link", display_text="🔗"),
        },
    )
else:
    st.info("Weekly report data building up...")

st.divider()

# ── SECTION 4: 📋 STATUS TRANSITIONS ─────────────────────────────────────────
st.subheader("📋 Recent Status Transitions")
st.caption("Products that changed status in the last 24 hours.")

transitions = _load_status_transitions()
if transitions:
    t_data = []
    for r in transitions[:20]:
        we = r.get("window_end")
        t_data.append({
            "Product": (r["title"] or r["external_id"])[:40],
            "🏪 Source": r["source"],
            "Status": r.get("status", "?").upper(),
            "🕐 Window": we.strftime("%m/%d %H:%M") if hasattr(we, "strftime") else "?",
        })
    st.dataframe(
        pd.DataFrame(t_data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(t_data) + 42, 500),
    )
else:
    st.info("No recent transitions — data builds as the scheduler runs.")

st.divider()

# ── SECTION 5: SYSTEM STATUS ─────────────────────────────────────────────────
st.subheader("🩺 Monitor Health")
try:
    health = storage.query(
        "SELECT source, state, last_run, last_ok, rows_in, error_rate "
        "FROM health ORDER BY source"
    )
    if health:
        h_data = []
        for h in health:
            h_data.append({
                "Source": h["source"],
                "State": h.get("state", "?").upper(),
                "Last Run": (
                    h["last_run"].strftime("%H:%M") if hasattr(h.get("last_run"), "strftime") else "?"
                ),
                "Rows": f"{h.get('rows_in', 0):,}",
                "Error Rate": f"{float(h.get('error_rate', 0) or 0) * 100:.1f}%",
            })
        st.dataframe(
            pd.DataFrame(h_data),
            use_container_width=True,
            hide_index=True,
            height=min(40 * len(h_data) + 40, 200),
        )
    else:
        st.info("Health data not yet available.")
except Exception:
    st.info("Health system not yet initialized.")

# ── TIMESTAMP ─────────────────────────────────────────────────────────────────
st.caption(f"\nUpdated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
