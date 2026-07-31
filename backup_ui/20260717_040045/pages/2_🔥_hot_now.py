"""🔥 HOT NOW — What's trending RIGHT NOW.

Real-time view of what's hot:
  - 🏆 Momentum Leaders — products with strongest price movement
  - ⚡ Breakout Watch — products that just broke into breakout/accelerating
  - 🚀 Accelerating — products gaining speed across timeframes
  - 🆕 New Finds — products detected for the first time recently
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import apply_professional_theme

st.set_page_config(page_title="Hot Now · trend-hunter", page_icon="🔥", layout="wide")
apply_professional_theme()
st.title("🔥 Hot Now")
st.caption("What's trending right now — updated every 30 seconds.")

st_autorefresh(interval=30_000, key="hot_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


# ── DATA LOADERS ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=20, show_spinner="Loading momentum leaders...")
def _load_momentum_leaders(limit: int = 20):
    """Products with the strongest price movement (highest |slope|)."""
    try:
        return storage.query(
            f"""
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
            WHERE a.n_points >= 2
            ORDER BY ABS(a.slope_pct_per_day) DESC
            LIMIT {int(limit)}
        """
        )
    except Exception:
        return []


@st.cache_data(ttl=20, show_spinner="Scanning for breakouts...")
def _load_breakout_products():
    """Products with breakout/accelerating patterns from cross_timeframe_patterns."""
    try:
        return storage.query(
            "SELECT source, external_id, title, pattern, status, "
            "slope_pct_per_day, window_end, detected_at "
            "FROM cross_timeframe_patterns "
            "WHERE pattern IN ('breakout', 'accelerating', 'rising') "
            "ORDER BY detected_at DESC, ABS(slope_pct_per_day) DESC "
            "LIMIT 30"
        )
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_new_finds(days: int = 7):
    """Products captured for the first time in the last N days."""
    try:
        return storage.query(
            f"""
            SELECT source, external_id, title, price, url, captured_at
            FROM products
            WHERE captured_at >= now() - INTERVAL {int(days)} DAY
            AND title IS NOT NULL
            ORDER BY captured_at DESC
            LIMIT 20
        """
        )
    except Exception:
        return []


@st.cache_data(ttl=15, show_spinner=False)
def _load_trending_counts():
    """Aggregate status counts across all products."""
    try:
        return storage.query(
            "SELECT status, count(*) as n FROM agg_product_status "
            "WHERE status IN ('rising', 'declining', 'stable', 'unknown') "
            "GROUP BY status ORDER BY n DESC"
        )
    except Exception:
        return []


# ── SECTION 1: MARKET SENTIMENT BAR ───────────────────────────────────────────st.subheader("📊 Market Pulse")

status_counts = _load_trending_counts()
if status_counts:
    status_map = {r["status"]: r["n"] for r in status_counts}
    sent_cols = st.columns(4)
    for col, label, _ in [
        (0, "🟢 Rising", "rising"),
        (1, "🔴 Declining", "declining"),
        (2, "🔵 Stable", "stable"),
        (3, "⚪ Unknown", "unknown"),
    ]:
        sent_cols[col].metric(
            label,
            status_map.get(label.split()[-1].lower(), 0),
        )
else:
    st.info("Status data building up...")

st.divider()

# ── SECTION 2: 🏆 MOMENTUM LEADERS ───────────────────────────────────────────
st.subheader("🏆 Momentum Leaders")
st.caption("Products with the strongest price movement — highest absolute slope across timeframes.")

leaders = _load_momentum_leaders(limit=20)
if not leaders:
    st.info("No classified products yet. Data builds after each scan cycle.")
else:
    # Group by product and find best slope per product
    product_map: dict = {}
    for r in leaders:
        key = (r["source"], r["external_id"])
        if key not in product_map:
            product_map[key] = {
                "source": r["source"],
                "external_id": r["external_id"],
                "title": (r["title"] or r["external_id"])[:45],
                "price": r.get("price"),
                "url": r.get("url") or "",
                "status": r.get("status", "?"),
                "best_slope": 0.0,
                "best_tf": 0,
                "all_slopes": [],
            }
        pm = product_map[key]
        slope = float(r.get("slope_pct_per_day", 0) or 0) * 100
        pm["all_slopes"].append(slope)
        if abs(slope) > abs(pm["best_slope"]):
            pm["best_slope"] = slope
            pm["best_tf"] = r.get("timeframe_days", 0)

    # Sort by absolute slope (momentum)
    scored = sorted(product_map.values(), key=lambda x: abs(x["best_slope"]), reverse=True)

    table_data = []
    for i, p in enumerate(scored[:15], 1):
        direction = "🚀" if p["best_slope"] > 0 else "📉"
        avg_slope = sum(p["all_slopes"]) / max(len(p["all_slopes"]), 1)
        table_data.append({
            "#": i,
            "Product": str(p["title"]),
            f"{direction} Slope%": f"{p['best_slope']:+.2f}%",
            "⏱️ Best TF": f"{p['best_tf']}d",
            "📊 Avg Slope": f"{avg_slope:+.2f}%",
            "💰 Price": f"${float(p['price']):.2f}" if p.get("price") else "—",
            "🏪 Source": p["source"],
            "🔗 Link": p.get("url", ""),
            "Status": p["status"].upper(),
        })

    st.dataframe(
        pd.DataFrame(table_data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(table_data) + 42, 600),
        column_config={
            "🔗 Link": st.column_config.LinkColumn(
                "🔗 Link",
                display_text="🛒 BUY NOW →",
                width="medium",
            ),
        },
    )

st.divider()

# ── SECTION 3: ⚡ BREAKOUT WATCH ─────────────────────────────────────────────
st.subheader("⚡ Breakout Watch")
st.caption("Products that have crossed into breakout, accelerating, or rising patterns.")

breakouts = _load_breakout_products()
if breakouts:
    # Group by product
    breakout_map: dict = {}
    for r in breakouts:
        key = (r["source"], r["external_id"])
        if key not in breakout_map:
            breakout_map[key] = {
                "source": r["source"],
                "external_id": r["external_id"],
                "title": (r["title"] or r["external_id"])[:45],
                "pattern": r["pattern"],
                "status": r.get("status", "?"),
                "slope": float(r.get("slope_pct_per_day", 0) or 0) * 100,
                "detected": r.get("detected_at"),
            }

    breakout_data = []
    for p in breakout_map.values():
        icon = {"breakout": "🚀", "accelerating": "⚡", "rising": "📈"}.get(
            p["pattern"], "❓"
        )
        breakout_data.append({
            "Product": p["title"],
            f"{icon} Pattern": p["pattern"].upper(),
            "📈 Slope": f"{p['slope']:+.2f}%/day",
            "🏪 Source": p["source"],
            "Status": p["status"].upper(),
            "🕐 Detected": (
                p["detected"].strftime("%m/%d %H:%M") if hasattr(p.get("detected"), "strftime") else "?"
            ),
        })

    st.dataframe(
        pd.DataFrame(breakout_data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(breakout_data) + 42, 500),
    )
else:
    st.info("No breakout patterns detected yet. Data builds as the scheduler collects more history.")

st.divider()

# ── SECTION 4: 🆕 NEW FINDS ───────────────────────────────────────────────────
st.subheader("🆕 New Finds (Last 7 Days)")
st.caption("Products detected for the first time recently.")

new_finds = _load_new_finds(days=7)
if new_finds:
    # Deduplicate
    seen = set()
    unique_new = []
    for r in new_finds:
        key = (r["source"], r["external_id"])
        if key not in seen:
            seen.add(key)
            unique_new.append(r)

    new_data = []
    for i, r in enumerate(unique_new[:15], 1):
        captured = r.get("captured_at")
        new_data.append({
            "#": i,
            "Product": (r["title"] or r["external_id"])[:45],
            "🏪 Source": r["source"],
            "💰 Price": f"${float(r['price']):.2f}" if r.get("price") else "—",
            "🔗 Link": r.get("url", ""),
            "🕐 Discovered": (
                captured.strftime("%m/%d %H:%M") if hasattr(captured, "strftime") else "?"
            ),
        })

    st.dataframe(
        pd.DataFrame(new_data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(new_data) + 42, 500),
        column_config={
            "🔗 Link": st.column_config.LinkColumn(
                "🔗 Link",
                display_text="🔗 OPEN →",
                width="medium",
            ),
        },
    )
else:
    st.info("No new products detected in the last 7 days.")

# ── TIMESTAMP ─────────────────────────────────────────────────────────────────
st.caption(f"\nUpdated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
