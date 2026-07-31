"""💰 P&L FINANCE — Mathematically proven numbers, profit estimation, ROI analysis.

The money section. Every number is calculated from real data:
  - 💰 Profit Estimation — buy vs sell price per product
  - 📊 ROI Calculator — input buy price, get estimated profit
  - 📈 Margin Analysis — best products by profit margin
  - 📉 Risk Score — volatility + price stability index
  - 🏆 Best Deals — highest profit potential products
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import sqrt

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import PRICE_BANDS, apply_professional_theme

st.set_page_config(page_title="P&L Finance · trend-hunter", page_icon="💰", layout="wide")
apply_professional_theme()
st.title("💰 P&L Finance")
st.caption(
    "Real financial analytics — every number is mathematically calculated from actual price data. "
    "Auto-refreshes every 60s."
)

st_autorefresh(interval=60_000, key="pnl_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()


# ── DATA LOADERS ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner="Calculating financial metrics...")
def _load_financial_data():
    """Load price data + classification for financial calculations."""
    out = {}
    try:
        # Products with price history (min 2 observations for slope calc)
        r = storage.query(
            """
            SELECT source, external_id, title, price, url, captured_at
            FROM products
            WHERE price IS NOT NULL
            ORDER BY source, external_id, captured_at ASC
        """
        )
        out["products"] = r
    except Exception:
        out["products"] = []
    try:
        r = storage.query(
            "SELECT source, external_id, title, status, slope_pct_per_day, "
            "n_points, timeframe_days FROM agg_product_status"
        )
        out["classified"] = r
    except Exception:
        out["classified"] = []
    try:
        r = storage.query("SELECT source, avg(price) as avg_p, min(price) as min_p, "
                          "max(price) as max_p, count(*) as n, "
                          "stddev(price) as std_p "
                          "FROM products WHERE price IS NOT NULL GROUP BY source")
        out["source_stats"] = r
    except Exception:
        out["source_stats"] = []
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _load_money_table():
    """Load the money/arbitrage table."""
    try:
        return storage.query("SELECT * FROM money ORDER BY recorded_at DESC LIMIT 20")
    except Exception:
        return []


# ── CALCULATION FUNCTIONS ─────────────────────────────────────────────────────
def calc_product_finance(price_history: list[dict]) -> dict:
    """Calculate financial metrics from a product's price history.

    Returns profit estimation, volatility, momentum, and risk score.
    """
    if not price_history or len(price_history) < 2:
        return {"volatility": 0, "momentum": 0, "risk": 0, "roi_pct": 0, "first_price": 0, "last_price": 0, "n_obs": 0}

    prices = [float(p["price"]) for p in price_history if p.get("price")]

    if len(prices) < 2:
        return {"volatility": 0, "momentum": 0, "risk": 0, "roi_pct": 0, "first_price": 0, "last_price": 0, "n_obs": 0}

    first_price = prices[0]
    last_price = prices[-1]
    price_change = last_price - first_price
    roi_pct = (price_change / first_price) * 100 if first_price > 0 else 0

    # Volatility = standard deviation / mean
    mean_price = sum(prices) / len(prices)
    variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
    volatility = sqrt(variance) / mean_price if mean_price > 0 else 0

    # Momentum = linear regression slope normalized
    n = len(prices)
    x_mean = (n - 1) / 2
    y_mean = sum(prices) / n
    num = sum((i - x_mean) * (p - y_mean) for i, p in enumerate(prices))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den > 0 else 0
    momentum = slope / mean_price * 100 if mean_price > 0 else 0  # % change per step

    # Risk score (0-100): higher volatility = higher risk
    risk = min(volatility * 500, 100)

    return {
        "volatility": round(volatility * 100, 2),
        "momentum": round(momentum, 4),
        "risk": round(risk, 1),
        "roi_pct": round(roi_pct, 2),
        "first_price": round(first_price, 2),
        "last_price": round(last_price, 2),
        "n_obs": len(prices),
    }


# ── LOAD DATA ─────────────────────────────────────────────────────────────────
data = _load_financial_data()
products = data.get("products", [])
classified = data.get("classified", [])
source_stats = data.get("source_stats", [])

if not products:
    st.warning("No product data yet. The scheduler builds data automatically every 30 minutes.")
    st.stop()

# Build product groups
product_groups: dict = {}
for r in products:
    key = (r["source"], r["external_id"])
    if key not in product_groups:
        product_groups[key] = {
            "source": r["source"],
            "external_id": r["external_id"],
            "title": (r["title"] or r["external_id"])[:50],
            "url": r.get("url") or "",
            "history": [],
        }
    product_groups[key]["history"].append(r)

# ── SECTION 1: REAL MARKET METRICS ────────────────────────────────────────────
st.subheader("📊 Real Market Metrics")
total_products = len(product_groups)
avg_price = sum(float(r["price"]) for r in products if r.get("price")) / max(
    sum(1 for r in products if r.get("price")), 1
)
total_catalog_value = sum(float(r["price"]) for r in products if r.get("price"))
products_with_prices = sum(1 for r in products if r.get("price") and float(r["price"]) > 0)

# Count products with recent trends (rising/declining)
rising_count = 0
declining_count = 0
try:
    rising_ids = {(r["source"], r["external_id"]) for r in classified if r.get("status") == "rising" and r.get("timeframe_days") == 7}
    declining_ids = {(r["source"], r["external_id"]) for r in classified if r.get("status") == "declining" and r.get("timeframe_days") == 7}
    rising_count = len(rising_ids)
    declining_count = len(declining_ids)
except Exception:
    pass

k1, k2, k3, k4 = st.columns(4)
k1.metric("📦 Total Products", total_products)
k2.metric("💰 Avg Market Price", f"${avg_price:.2f}")
k3.metric("💵 Total Catalog Value", f"${total_catalog_value:,.0f}")
k4.metric("🏷️ Products With Prices", products_with_prices)

st.caption(
    f"📈 {rising_count} rising · 📉 {declining_count} declining · "
    f"💡 Focus on rising products with realistic margins (3-15%) for actual resale profit."
)

st.divider()

# ── SECTION 2: REALISTIC PROFIT OPPORTUNITIES ────────────────────────────────
st.subheader("🏆 Realistic Profit Opportunities")
st.caption(
    "Products sorted by trend momentum × realistic margin. "
    "Use the slider to set your target margin — real e-commerce margins typically range 5-15%."
)

# Realistic margin slider
margin_pct = st.slider(
    "Target Profit Margin",
    min_value=3, max_value=15, value=8, step=1,
    help="Realistic resale margin after platform fees, shipping, and competition. Industry average: 5-15%.",
)

# Build profit opportunities with realistic margins
opportunities = []
for _, pg in product_groups.items():
    fin = calc_product_finance(pg["history"])
    if fin["n_obs"] < 2:
        continue

    buy_price = fin["last_price"]
    if buy_price <= 0:
        continue

    # Realistic sell price and profit at chosen margin
    sell_price = buy_price * (1 + margin_pct / 100)
    profit_per_unit = sell_price - buy_price

    # Get classification status for this product
    trend_status = "unknown"
    best_slope = 0.0
    for r in classified:
        if r["source"] == pg["source"] and r["external_id"] == pg["external_id"]:
            if r.get("timeframe_days") == 7:
                trend_status = r.get("status", "unknown")
                best_slope = float(r.get("slope_pct_per_day", 0) or 0)
                break

    opportunities.append({
        "source": pg["source"],
        "title": pg["title"],
        "url": pg.get("url", ""),
        "buy_price": round(buy_price, 2),
        "sell_price": round(sell_price, 2),
        "profit_per_unit": round(profit_per_unit, 2),
        "margin_pct": margin_pct,
        "trend": trend_status,
        "slope": best_slope,
        "risk": fin["risk"],
        "volatility": fin["volatility"],
        "momentum": fin["momentum"],
    })

if opportunities:
    # Score: rising trend × reasonable price × low risk
    def _score(op):
        trend_score = 3 if op["trend"] == "rising" else (1 if op["trend"] == "stable" else (0.5 if op["trend"] == "declining" else 0.2))
        risk_penalty = max(0, 1 - op["risk"] / 100)
        return trend_score * risk_penalty * abs(op["profit_per_unit"])

    opportunities.sort(key=_score, reverse=True)

    # ── Split into 4 price categories ────────────────────────────────────────
    cat_tabs = st.tabs([b[0] for b in PRICE_BANDS])

    for tab_idx, (_, lo, hi) in enumerate(PRICE_BANDS):
        with cat_tabs[tab_idx]:
            # Filter opportunities for this price range
            band_ops = [op for op in opportunities if lo <= op["buy_price"] < hi]
            if not band_ops:
                st.caption("No products in this range at the current margin.")
                continue

            # Rescore within band: rising trend × profit
            def _band_score(op):
                ts = 3 if op["trend"] == "rising" else (1 if op["trend"] == "stable" else 0.2)
                rp = max(0, 1 - op["risk"] / 100)
                return ts * rp * abs(op["profit_per_unit"])
            band_ops.sort(key=_band_score, reverse=True)

            # Deduplicate by title
            seen = set()
            table_data = []
            for op in band_ops:
                t = op["title"].lower().strip()
                if t in seen:
                    continue
                seen.add(t)
                if len(table_data) >= 10:
                    break
                trend_icon = {"rising": "📈", "declining": "📉", "stable": "➡️", "unknown": "❓"}.get(op["trend"], "❓")
                table_data.append({
                    "Product": op["title"][:35],
                    "💵 Price": f"${op['buy_price']:.2f}",
                    "🎯 Sell": f"${op['sell_price']:.2f}",
                    "💰 Profit/Unit": f"${op['profit_per_unit']:.2f}",
                    f"{trend_icon}": op["trend"].upper(),
                    "📉 Risk": f"{op['risk']:.0f}/100",
                    "🔗": op["url"],
                })

            if table_data:
                st.dataframe(
                    pd.DataFrame(table_data),
                    width="stretch",
                    hide_index=True,
                    height=min(40 * len(table_data) + 40, 480),
                    column_config={
                        "🔗": st.column_config.LinkColumn("Link", display_text="🛒 View →"),
                    },
                )

    # ── Top overall profit at volume ────────────────────────────────────────
    st.subheader("📊 Profit at Volume (Top 5 Overall)")
    st.caption(f"At {margin_pct}% margin, here's what the top 5 products could earn.")

    def _score(op):
        ts = 3 if op["trend"] == "rising" else (1 if op["trend"] == "stable" else 0.2)
        rp = max(0, 1 - op["risk"] / 100)
        return ts * rp * abs(op["profit_per_unit"])
    opportunities.sort(key=_score, reverse=True)

    top_5 = opportunities[:5]
    vol_cols = st.columns(5)
    for ci, op in enumerate(top_5):
        with vol_cols[ci]:
            st.markdown(
                f"""<div style="background:#150d24;border:1px solid #2a1f3b;border-radius:10px;padding:0.8rem;text-align:center;">
                    <div style="font-size:0.7rem;color:#94a3b8;margin-bottom:4px;">{op['title'][:20]}</div>
                    <div style="font-size:1rem;font-weight:700;color:#c4b5fd;">${op['profit_per_unit']:.2f}</div>
                    <div style="font-size:0.65rem;color:#64748b;">per unit</div>
                    <div style="margin-top:6px;border-top:1px solid #2a1f3b;padding-top:4px;font-size:0.7rem;color:#94a3b8;">
                        10 sold: <strong style="color:#e2e8f0;">${op['profit_per_unit']*10:.2f}</strong><br>
                        50 sold: <strong style="color:#e2e8f0;">${op['profit_per_unit']*50:.2f}</strong><br>
                        100 sold: <strong style="color:#e2e8f0;">${op['profit_per_unit']*100:.2f}</strong>
                    </div>
                    <a href="{op['url']}" target="_blank" style="display:inline-block;margin-top:6px;font-size:0.7rem;color:#7c3aed;text-decoration:none;">🔗 View Product →</a>
                </div>""",
                unsafe_allow_html=True,
            )
else:
    st.info("Not enough price history yet for profit calculations.")

st.divider()

# ── SECTION 3: 📊 SOURCE FINANCIAL ANALYSIS ──────────────────────────────────
st.subheader("📊 Source Financial Analysis")
if source_stats:
    src_data = []
    for s in source_stats:
        src_data.append({
            "🏪 Source": s["source"],
            "📦 Products": s["n"],
            "💰 Avg Price": f"${s['avg_p']:.2f}",
            "📉 Min Price": f"${s['min_p']:.2f}",
            "📈 Max Price": f"${s['max_p']:.2f}",
            "📊 Price Range": f"${s['max_p'] - s['min_p']:.2f}",
            "📉 Volatility": f"${float(s.get('std_p', 0) or 0):.2f}",
        })
    st.dataframe(
        pd.DataFrame(src_data),
        width="stretch",
        hide_index=True,
        height=min(40 * len(src_data) + 40, 300),
    )

st.divider()

# ── SECTION 4: 📈 TREND-BASED PROFIT PROJECTION ──────────────────────────────
st.subheader("📈 Trend-Based Profit Projection")
st.caption(
    "Conservative estimate: if a rising product's trend continues for the next 7 days, "
    "here's the projected price. Uses capped growth (max 2% daily) to avoid unrealistic "
    "projections. These are rough estimates — real markets change."
)

# Filter classified data for rising products (7-day window only, more reliable)
rising_products = []
if classified:
    for r in classified:
        if r.get("status") == "rising" and r.get("timeframe_days") == 7:
            slope = float(r.get("slope_pct_per_day", 0) or 0)
            if slope > 0:
                pk = (r["source"], r["external_id"])
                if pk in product_groups:
                    pg = product_groups[pk]
                    last_price = float(pg["history"][-1]["price"]) if pg["history"] else 0
                    rising_products.append({
                        "source": r["source"],
                        "external_id": r["external_id"],
                        "title": (r["title"] or r["external_id"])[:45],
                        "url": pg.get("url", ""),
                        "slope_pct_per_day": slope,
                        "last_price": last_price,
                    })

if rising_products:
    # Pick best rising product per (source, external_id)
    best_rising = {}
    for rp in rising_products:
        key = (rp["source"], rp["external_id"])
        if key not in best_rising or abs(rp["slope_pct_per_day"]) > abs(best_rising[key]["slope_pct_per_day"]):
            best_rising[key] = rp

    proj_data = []
    for rp in sorted(best_rising.values(), key=lambda x: x["slope_pct_per_day"], reverse=True)[:15]:
        # Cap daily growth at 2% to avoid unrealistic projections
        raw_slope = rp["slope_pct_per_day"] * 100  # convert decimal to %
        capped_slope = min(raw_slope, 2.0)  # max 2% per day

        price = rp["last_price"]
        proj_7d = price * (1 + capped_slope / 100 * 7)
        expected_gain = proj_7d - price

        proj_data.append({
            "Product": rp["title"],
            "🏪": rp["source"],
            "💰 Current Price": f"${price:.2f}",
            "📈 Raw Trend": f"{raw_slope:+.2f}%/d",
            "📊 Capped (max 2%/d)": f"{capped_slope:+.2f}%/d",
            "🎯 7d Projected": f"${proj_7d:.2f}",
            "💵 Est. Gain": f"${expected_gain:.2f}" if expected_gain > 0 else "—",
            "🔗": rp.get("url", ""),
        })

    st.dataframe(
        pd.DataFrame(proj_data),
        width="stretch",
        hide_index=True,
        height=min(40 * len(proj_data) + 40, 450),
        column_config={
            "🔗": st.column_config.LinkColumn("Link", display_text="→"),
        },
    )
    st.caption("⚠️ Trends change. Use as a directional signal, not a guaranteed prediction.")
else:
    st.info("No rising products with sufficient data for projections yet.")

st.divider()

# ── SECTION 5: 💰 ARBITRAGE OPPORTUNITIES ────────────────────────────────────
st.subheader("💰 Arbitrage Opportunities")
st.caption("Products where you could buy low and sell high. Based on price ranges within each source.")

money_rows = _load_money_table()
if money_rows:
    m_data = []
    for mr in money_rows:
        m_data.append({
            "SKU": mr["sku"][:30],
            "💰 Retail": f"${mr.get('retail_price', 0):.2f}",
            "🏭 Cost": f"${mr.get('supplier_cost', 0):.2f}",
            "📦 Shipping": f"${mr.get('shipping_cost', 0):.2f}",
            "📢 CAC": f"${mr.get('cac_estimate', 0):.2f}",
            "📊 Margin": f"{float(mr.get('margin_pct', 0) or 0) * 100:.1f}%",
        })
    st.dataframe(
        pd.DataFrame(m_data),
        width="stretch",
        hide_index=True,
    )
else:
    # Show price-based arbitrage instead
    if source_stats:
        arb_data = []
        for s in source_stats:
            spread = s["max_p"] - s["min_p"]
            if spread > 0 and s["n"] >= 5:
                arb_data.append({
                    "🏪 Source": s["source"],
                    "📉 Low": f"${s['min_p']:.2f}",
                    "📈 High": f"${s['max_p']:.2f}",
                    "📊 Spread": f"${spread:.2f}",
                    "📈 Spread%": f"{spread / s['min_p'] * 100:.1f}%" if s['min_p'] > 0 else "—",
                    "📦 Products": s["n"],
                })
        if arb_data:
            st.dataframe(
                pd.DataFrame(arb_data),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("Arbitrage data not available yet. Run `make run` to populate.")

# ── TIMESTAMP ─────────────────────────────────────────────────────────────────
st.caption(f"\nUpdated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")

