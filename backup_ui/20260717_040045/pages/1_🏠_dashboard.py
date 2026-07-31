"""🏠 DASHBOARD — Live metrics, Top 10, quick charts, and recent alerts.

The central command center. Shows everything that matters at a glance:
  - Live system status (scheduler, sources, data volume)
  - Top 10 ranked products with momentum scores across 7 timeframes
  - Price history sparklines for top products
  - Recent breakout/pattern-change alerts
  - Source health snapshot
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import apply_professional_theme

st.set_page_config(page_title="Dashboard · trend-hunter", page_icon="🏠", layout="wide")
apply_professional_theme()
st.title("🏠 Live Dashboard")
st.caption(
    "Everything at a glance — auto-refreshes every 30s. "
    "Scheduler runs automatically every 15 minutes."
)

# Auto-refresh every 30 seconds
st_autorefresh(interval=30_000, key="dash_refresh")

# ── storage ───────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


# ── DATA LOADERS ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=20, show_spinner="Loading metrics...")
def _load_metrics():
    """Load aggregate counts and source-level stats."""
    out = {}
    try:
        r = storage.query("SELECT count(*) as cnt FROM products")
        out["products"] = r[0]["cnt"] if r else 0
    except Exception:
        out["products"] = 0
    try:
        r = storage.query("SELECT count(*) as cnt FROM agg_product_status")
        out["classified"] = r[0]["cnt"] if r else 0
    except Exception:
        out["classified"] = 0
    try:
        r = storage.query("SELECT count(*) as cnt FROM trend_history")
        out["history"] = r[0]["cnt"] if r else 0
    except Exception:
        out["history"] = 0
    try:
        r = storage.query(
            "SELECT source, count(DISTINCT external_id) as products, "
            "count(*) as obs, max(captured_at) as last_scan "
            "FROM products WHERE price IS NOT NULL "
            "GROUP BY source ORDER BY products DESC"
        )
        out["sources"] = r
    except Exception:
        out["sources"] = []
    try:
        r = storage.query(
            "SELECT source, state, last_run, rows_in, error_rate "
            "FROM health ORDER BY source"
        )
        out["health"] = r
    except Exception:
        out["health"] = []
    try:
        r = storage.query("SELECT count(*) as cnt FROM cross_timeframe_patterns")
        out["patterns"] = r[0]["cnt"] if r else 0
    except Exception:
        out["patterns"] = 0
    return out


@st.cache_data(ttl=20, show_spinner="Ranking top products...")
def _load_top_products(limit: int = 10):
    """Load top products across all timeframes with price + URL."""
    try:
        return storage.query(
            f"""
            SELECT a.source, a.external_id, a.title, a.status,
                   a.slope_pct_per_day, a.n_points, a.timeframe_days,
                   p.price, p.currency, p.url, p.captured_at
            FROM agg_product_status a
            LEFT JOIN (
                SELECT source, external_id,
                       FIRST(price) as price,
                       FIRST(currency) as currency,
                       FIRST(url) as url,
                       MAX(captured_at) as captured_at
                FROM products
                WHERE price IS NOT NULL
                GROUP BY source, external_id
            ) p ON a.source = p.source AND a.external_id = p.external_id
            WHERE a.n_points > 0
            ORDER BY a.n_points DESC, ABS(a.slope_pct_per_day) DESC
            LIMIT {int(limit) * 5}
        """
        )
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_recent_alerts():
    """Load recent cross-timeframe pattern alerts."""
    try:
        r = storage.query(
            "SELECT source, external_id, title, pattern, status, "
            "slope_pct_per_day, window_end, detected_at "
            "FROM cross_timeframe_patterns "
            "WHERE pattern NOT IN ('steady', 'unknown') "
            "ORDER BY detected_at DESC LIMIT 20"
        )
        if r:
            return r
    except Exception:
        pass
    return []


# ── SECTION 1: SYSTEM STATUS ──────────────────────────────────────────────────
st.subheader("📡 System Status")
metrics = _load_metrics()

kpi_cols = st.columns(5)
kpi_cols[0].metric("📦 Total Products", f"{metrics['products']:,}")
kpi_cols[1].metric("📊 Classified", f"{metrics['classified']:,}")
kpi_cols[2].metric("📈 History Points", f"{metrics['history']:,}")
kpi_cols[3].metric("🔍 Pattern Snapshots", f"{metrics['patterns']:,}")
kpi_cols[4].metric("🏪 Active Sources", len(metrics["sources"]))

if metrics["sources"]:
    st.caption("Sources with recent data:")
    src_cols = st.columns(min(4, len(metrics["sources"])))
    for i, src in enumerate(metrics["sources"][:4]):
        last_scan = src.get("last_scan")
        time_str = (
            last_scan.strftime("%H:%M UTC") if hasattr(last_scan, "strftime") else "?"
        )
        src_cols[i].metric(
            src["source"],
            f"{src['products']} products",
            f"{src['obs']} obs · {time_str}",
        )

st.divider()

# ── SECTION 2: 🏆 TOP 10 PRODUCTS ─────────────────────────────────────────────
st.subheader("🏆 Top Products by Momentum")
st.caption("Ranked by data volume × price movement across all timeframes. Links go to store pages.")

top_rows = _load_top_products(limit=10)
if not top_rows:
    st.info("No classified products yet. The scheduler builds data automatically every 15 minutes.")
else:
    # Build per-product aggregates across timeframes
    product_map: dict = {}
    for r in top_rows:
        key = (r["source"], r["external_id"])
        if key not in product_map:
            product_map[key] = {
                "source": r["source"],
                "external_id": r["external_id"],
                "title": (r["title"] or r["external_id"])[:50],
                "price": r.get("price"),
                "currency": r.get("currency", "USD"),
                "url": r.get("url") or "",
                "status": r.get("status", "unknown"),
                "total_points": 0,
                "timeframes": {},
            }
        pm = product_map[key]
        pm["total_points"] += r.get("n_points", 0) or 0
        tf = r.get("timeframe_days")
        if tf:
            pm["timeframes"][tf] = {
                "slope": r.get("slope_pct_per_day", 0) or 0,
                "n": r.get("n_points", 0) or 0,
                "status": r.get("status", "unknown"),
            }

    # Sort by total points (data density) then by average absolute slope
    scored = []
    for pm in product_map.values():
        slopes = [abs(tf["slope"]) for tf in pm["timeframes"].values()]
        avg_slope = sum(slopes) / max(len(slopes), 1) * 100  # convert to %
        momentum = min(pm["total_points"] * 0.5 + avg_slope * 10, 100)
        scored.append({**pm, "momentum": round(momentum, 1), "avg_slope_pct": round(avg_slope, 2)})

    scored.sort(key=lambda x: x["momentum"], reverse=True)

    # Display top 10
    table_data = []
    for i, p in enumerate(scored[:10], 1):
        row = {
            "#": i,
            "Product": str(p["title"])[:45],
            "🏪 Source": p["source"],
            "💰 Price": f"${float(p['price']):.2f}" if p.get("price") else "—",
            "🔗 Link": p.get("url", ""),
            "⚡ Score": f"{p['momentum']}/100",
            "📊 Data Pts": p["total_points"],
        }
        for tf_label, tf_key in [
            ("1d%", 1),
            ("7d%", 7),
            ("14d%", 14),
            ("30d%", 30),
            ("90d%", 90),
        ]:
            tf_data = p["timeframes"].get(tf_key)
            if tf_data:
                slope_pct = tf_data["slope"] * 100
                row[tf_label] = f"{slope_pct:+.2f}%"
            else:
                row[tf_label] = "—"
        table_data.append(row)

    df = pd.DataFrame(table_data)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(df) + 42, 520),
        column_config={
            "🔗 Link": st.column_config.LinkColumn(
                "🔗 Link",
                display_text="🛒 BUY NOW →",
                width="medium",
            ),
        },
    )

    # ── Price chart for top 3 ─────────────────────────────────────────────
    st.subheader("📈 Price History — Top 3 Products")
    chart_cols = st.columns(3)
    for col_i, p in enumerate(scored[:3]):
        with chart_cols[col_i]:
            st.caption(f"**{p['title'][:30]}** ({p['source']})")
            try:
                pts = storage.query(
                    "SELECT captured_at, price FROM products "
                    "WHERE source=? AND external_id=? AND price IS NOT NULL "
                    "ORDER BY captured_at ASC LIMIT 100",
                    (p["source"], p["external_id"]),
                )
                if pts and len(pts) > 1:
                    df_pts = pd.DataFrame(pts)
                    df_pts["captured_at"] = pd.to_datetime(df_pts["captured_at"])
                    df_pts = df_pts.drop_duplicates("captured_at").set_index("captured_at")
                    st.line_chart(df_pts["price"], height=150)
                else:
                    st.caption("⏳ Building history...")
            except Exception:
                st.caption("⏳ Not enough data yet")

st.divider()

# ── SECTION 3: RECENT ALERTS / BREAKOUTS ─────────────────────────────────────
st.subheader("🔔 Recent Breakout & Momentum Alerts")
alerts = _load_recent_alerts()
if alerts:
    alert_data = []
    for a in alerts[:10]:
        slope_pct = float(a.get("slope_pct_per_day", 0) or 0) * 100
        alert_data.append({
            "Product": (a["title"] or a["external_id"])[:40],
            "🏪 Source": a["source"],
            "🔍 Pattern": a["pattern"].upper(),
            "📊 Status": a.get("status", "?").upper(),
            "📈 Slope": f"{slope_pct:+.2f}%/day",
            "🕐 Detected": (
                a["detected_at"].strftime("%m/%d %H:%M")
                if hasattr(a["detected_at"], "strftime")
                else "?"
            ),
        })
    st.dataframe(
        pd.DataFrame(alert_data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(alert_data) + 42, 420),
    )
else:
    st.info("No breakout alerts yet. Patterns build up as the scheduler collects data over time.")

st.divider()

# ── SECTION 4: SOURCE HEALTH ─────────────────────────────────────────────────
st.subheader("🩺 Source Health")
health_rows = metrics.get("health", [])
if health_rows:
    health_data = []
    for h in health_rows:
        health_data.append({
            "Source": h["source"],
            "State": h.get("state", "?").upper(),
            "Last Run": (
                h["last_run"].strftime("%m/%d %H:%M") if hasattr(h.get("last_run"), "strftime") else "?"
            ),
            "Rows In": f"{h.get('rows_in', 0):,}",
            "Error Rate": f"{float(h.get('error_rate', 0) or 0) * 100:.1f}%",
        })
    st.dataframe(
        pd.DataFrame(health_data),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(health_data) + 42, 250),
    )
else:
    st.info("Health data building up after first scan cycle.")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: 🏆 TOP 10 INFLUENCER PROFILES
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("🏆 Top Influencer Stores")
st.caption("Store profiles ranked by product volume and total catalog value.")


@st.cache_data(ttl=30, show_spinner=False)
def _load_influencer_profiles():
    """Build influencer/store profiles from product data."""
    try:
        rows = storage.query(
            "SELECT source, external_id, title, price, url, captured_at "
            "FROM products "
            "WHERE price IS NOT NULL AND price > 0 AND url IS NOT NULL AND url != ''"
        )
    except Exception:
        return []

    stores = defaultdict(lambda: {"products": [], "total_value": 0.0, "prices": []})
    for row in rows:
        url = row.get("url", "")
        try:
            domain = urlparse(url).netloc.replace("www.", "").split(".")[0] if url else "unknown"
        except Exception:
            domain = "unknown"

        price = float(row["price"]) if row.get("price") else 0
        stores[domain]["products"].append(row)
        stores[domain]["total_value"] += price
        stores[domain]["prices"].append(price)

    profiles = []
    for name, data in stores.items():
        avg_p = sum(data["prices"]) / len(data["prices"]) if data["prices"] else 0
        profiles.append({
            "name": name.capitalize(),
            "product_count": len(data["products"]),
            "avg_price": round(avg_p, 2),
            "total_value": round(data["total_value"], 2),
            "top_products": sorted(data["products"], key=lambda x: float(x.get("price", 0) or 0), reverse=True)[:3],
        })

    profiles.sort(key=lambda x: x["product_count"], reverse=True)
    return profiles[:10]


profiles = _load_influencer_profiles()

if profiles:
    # Profile cards in a grid
    for row_start in range(0, len(profiles), 3):
        cols = st.columns(3)
        for col_i, prof in enumerate(profiles[row_start : row_start + 3]):
            with cols[col_i]:
                # Build top product names
                top_items = []
                for tp in prof["top_products"]:
                    title = str(tp.get("title", "") or "")[:30]
                    price = float(tp.get("price", 0) or 0)
                    top_items.append(f"• {title} — ${price:.2f}")

                st.markdown(
                    f"""<div class="nav-card" style="padding:1.1rem;">
                        <div style="font-size:1.15rem;font-weight:700;color:#e2e8f0;margin-bottom:2px;">
                            🏪 {prof['name']}
                        </div>
                        <div style="display:flex;gap:1rem;margin:8px 0 10px 0;">
                            <div><span style="color:#94a3b8;font-size:0.75rem;">Products</span><br>
                                 <span style="color:#c4b5fd;font-size:1.1rem;font-weight:700;">{prof['product_count']}</span></div>
                            <div><span style="color:#94a3b8;font-size:0.75rem;">Avg Price</span><br>
                                 <span style="color:#e2e8f0;font-size:1.1rem;font-weight:700;">${prof['avg_price']:.2f}</span></div>
                            <div><span style="color:#94a3b8;font-size:0.75rem;">Catalog Value</span><br>
                                 <span style="color:#7c3aed;font-size:1.1rem;font-weight:700;">${prof['total_value']:,.0f}</span></div>
                        </div>
                        <div style="font-size:0.8rem;color:#64748b;border-top:1px solid #2a1f3b;padding-top:8px;">
                            <strong style="color:#94a3b8;">Top Products:</strong><br>
                            {"<br>".join(top_items)}
                        </div>
                    </div>""",
                    unsafe_allow_html=True,
                )
else:
    st.info("Store profiles building up as data is collected.")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: 📚 HOW TO USE THIS SYSTEM
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("📚 How to Use trend-hunter")

with st.expander("🎯 **Finding Trending Products**"):
    st.markdown(
        """
    1. **Check the 🔥 Hot Now page** — See real-time momentum leaders, breakout products, and new finds from the last 7 days.
    2. **Browse the 📈 Analytics page** — Explore products by store category and see cross-timeframe trend signals.
    3. **Watch the Market Pulse** — At the top of Hot Now, the Rising/Declining/Stable counts tell you at a glance whether the market is bullish or bearish.
    4. **Use the 📊 Top 10 table** (above) — Products ranked by momentum score across 1-day, 7-day, 14-day, 30-day, and 90-day windows.
        """
    )

with st.expander("💰 **Calculating Profit Potential**"):
    st.markdown(
        """
    1. **Go to the 📋 Tasks tab** — Search for any product by name (e.g. "shoe", "lip", "cream").
    2. **Add products** (max 20) — Each product shows buy price, sell price (with markup), and profit per unit.
    3. **Adjust the markup slider** (5%-200%) — Set your target profit margin.
    4. **See instant projections** — The task board calculates:
       - 💵 Profit per unit at your markup
       - 📈 Daily profit at 10/50/100 sales
       - 📆 Monthly & yearly cumulative projections
       - 🚀 Timeline cards (1mo, 3mo, 6mo, 12mo) at 50 sales/day
        """
    )

with st.expander("🛒 **Finding Buy Links & Vendor Info**"):
    st.markdown(
        """
    1. **Go to the 🛒 Buy page** — Every product is listed with a clickable **Buy Now →** link.
    2. **Browse by vendor** — The Vendor Directory shows each store's stats and top products.
    3. **Search by product name** — Use the search bar to find specific products quickly.
    4. **Compare prices** — See price history and current price for every product.
        """
    )

with st.expander("📡 **Monitoring & Alerts**"):
    st.markdown(
        """
    1. **The 📡 Monitor page** shows real-time pattern alerts — when a product's trend changes.
    2. **Status transitions are tracked** — See when products go from stable → rising or rising → declining.
    3. **The system auto-scans every 15 minutes** — Fresh data without any manual work.
    4. **Dashboard auto-refreshes** — All pages update automatically.
        """
    )

with st.expander("⚙️ **Managing Settings & Sources**"):
    st.markdown(
        """
    1. **The ⚙️ Config page** lets you manage data sources, scan intervals, and API keys.
    2. **Add new Shopify stores** by entering a name, URL, and API key.
    3. **Run manual scans** anytime with the ▶️ Run Scan Now button.
    4. **Check system health** with the 🩺 Run Doctor diagnostic tool.
        """
    )

with st.expander("📊 **Interpreting Trend Statuses**"):
    st.markdown(
        """
    | Status | Meaning |
    |--------|---------|
    | 🟢 **Rising** | Price consistently increasing — strong momentum |
    | 🔴 **Declining** | Price consistently decreasing — potential discount opportunity |
    | 🔵 **Stable** | Price holding steady — low volatility |
    | ⚪ **Unknown** | Not enough data yet — needs more observations |

    Products with **Rising** status on multiple timeframes are your best opportunities.
    Products with **Declining** status may be good for short-selling or identifying market shifts.
        """
    )

st.divider()

# ── TIMESTAMP ─────────────────────────────────────────────────────────────────
st.caption(f"Last updated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
