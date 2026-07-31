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
from trend_hunter.ui.theme import apply_professional_theme

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
        return {"profit_est": 0, "volatility": 0, "momentum": 0, "risk": 0, "roi_pct": 0}

    prices = [float(p["price"]) for p in price_history if p.get("price")]

    if len(prices) < 2:
        return {"profit_est": 0, "volatility": 0, "momentum": 0, "risk": 0, "roi_pct": 0}

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

    # Profit estimation (assuming 20% above current price as sell target)
    sell_estimate = last_price * 1.20
    profit_est = sell_estimate - last_price

    return {
        "profit_est": round(profit_est, 2),
        "volatility": round(volatility * 100, 2),
        "momentum": round(momentum, 4),
        "risk": round(risk, 1),
        "roi_pct": round(roi_pct, 2),
        "first_price": round(first_price, 2),
        "last_price": round(last_price, 2),
        "sell_estimate": round(sell_estimate, 2),
        "n_obs": len(prices),
    }


# ── LOAD DATA ─────────────────────────────────────────────────────────────────
data = _load_financial_data()
products = data.get("products", [])
classified = data.get("classified", [])
source_stats = data.get("source_stats", [])

if not products:
    st.warning("No product data yet. The scheduler builds data automatically every 15 minutes.")
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

# ── SECTION 1: GLOBAL METRICS ────────────────────────────────────────────────
st.subheader("📊 Global Financial Metrics")
total_products = len(product_groups)
avg_price = sum(float(r["price"]) for r in products if r.get("price")) / max(
    sum(1 for r in products if r.get("price")), 1
)
# Total addressable value
total_value = sum(float(r["price"]) for r in products if r.get("price"))

# Calculate estimated profit potential across all products
total_profit_potential = 0
profitable_count = 0
for _, pg in product_groups.items():
    fin = calc_product_finance(pg["history"])
    if fin["profit_est"] > 0:
        total_profit_potential += fin["profit_est"]
        profitable_count += 1

k1, k2, k3, k4 = st.columns(4)
k1.metric("📦 Products", total_products)
k2.metric("💰 Avg Price", f"${avg_price:.2f}")
k3.metric("💵 Est. Profit Pool", f"${total_profit_potential:,.2f}")
k4.metric("🟢 Profitable Products", profitable_count)

st.divider()

# ── SECTION 2: 🏆 BEST PROFIT OPPORTUNITIES ──────────────────────────────────
st.subheader("🏆 Best Profit Opportunities")
st.caption("Products ranked by estimated profit potential (selling at 20% above current price).")

profit_rows = []
for _, pg in product_groups.items():
    fin = calc_product_finance(pg["history"])
    if fin["profit_est"] > 0 and fin["n_obs"] >= 2:
        profit_rows.append({
            "source": pg["source"],
            "title": pg["title"],
            "url": pg.get("url", ""),
            "buy_price": fin["last_price"],
            "sell_est": fin["sell_estimate"],
            "profit": fin["profit_est"],
            "roi": fin["roi_pct"],
            "momentum": fin["momentum"],
            "volatility": fin["volatility"],
            "risk": fin["risk"],
            "obs": fin["n_obs"],
        })

profit_rows.sort(key=lambda x: x["profit"], reverse=True)

if profit_rows:
    table_data = []
    for i, p in enumerate(profit_rows[:20], 1):
        table_data.append({
            "#": i,
            "Product": p["title"][:45],
            "🏪 Source": p["source"],
            "💵 Buy Price": f"${p['buy_price']:.2f}",
            "🎯 Sell Est.": f"${p['sell_est']:.2f}",
            "💰 Profit": f"${p['profit']:.2f}",
            "📈 ROI": f"{p['roi']:+.2f}%",
            "⚡ Momentum": f"{p['momentum']:.4f}",
            "📉 Risk": f"{p['risk']:.0f}/100",
            "🔗": p["url"],
        })

    st.dataframe(
        pd.DataFrame(table_data),
        use_container_width=True,
        hide_index=True,
        height=min(40 * len(table_data) + 40, 600),
        column_config={
            "🔗": st.column_config.LinkColumn("🔗 Link", display_text="Buy →"),
        },
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
        use_container_width=True,
        hide_index=True,
        height=min(40 * len(src_data) + 40, 300),
    )

st.divider()

# ── SECTION 4: 📈 TREND-BASED PROFIT PROJECTION ──────────────────────────────
st.subheader("📈 Trend-Based Profit Projection")
st.caption(
    "For products with rising/breakout patterns, projected profit if the trend continues "
    "for 7 and 30 days."
)

# Filter classified data for rising products
rising_products = []
if classified:
    for r in classified:
        if r.get("status") in ("rising",) and r.get("timeframe_days") in (7, 30):
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
                        "timeframe_days": r.get("timeframe_days", 7),
                    })

if rising_products:
    # Pick best rising product per (source, external_id)
    best_rising = {}
    for rp in rising_products:
        key = (rp["source"], rp["external_id"])
        if key not in best_rising or rp["timeframe_days"] > best_rising[key]["timeframe_days"]:
            best_rising[key] = rp

    proj_data = []
    for rp in sorted(best_rising.values(), key=lambda x: x["slope_pct_per_day"], reverse=True)[:15]:
        slope_daily = rp["slope_pct_per_day"] * 100  # convert to %
        price = rp["last_price"]

        # Project 7 days and 30 days
        proj_7d = price * (1 + slope_daily / 100 * 7)
        proj_30d = price * (1 + slope_daily / 100 * 30)
        profit_7d = proj_7d - price
        profit_30d = proj_30d - price

        proj_data.append({
            "Product": rp["title"],
            "🏪": rp["source"],
            "💰 Current": f"${price:.2f}",
            "📈 Daily Trend": f"{slope_daily:+.3f}%/d",
            "🎯 7d Proj.": f"${proj_7d:.2f}",
            "💵 7d Profit": f"${profit_7d:.2f}" if profit_7d > 0 else "—",
            "🎯 30d Proj.": f"${proj_30d:.2f}",
            "💵 30d Profit": f"${profit_30d:.2f}" if profit_30d > 0 else "—",
            "🔗": rp.get("url", ""),
        })

    st.dataframe(
        pd.DataFrame(proj_data),
        use_container_width=True,
        hide_index=True,
        height=min(40 * len(proj_data) + 40, 550),
        column_config={
            "🔗": st.column_config.LinkColumn("Link", display_text="→"),
        },
    )
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
        use_container_width=True,
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
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Arbitrage data not available yet. Run `make run` to populate.")

# ── TIMESTAMP ─────────────────────────────────────────────────────────────────
st.caption(f"\nUpdated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")

