"""📋 Product Tasks — Add up to 20 products, get instant profit & business projections.

Your personal task board:
  - 🔍 Search & add products (max 20) from Shopify stores
  - 💰 Instant profit calculation with adjustable markup
  - 📈 Timeline projections: 1mo, 3mo, 6mo, 12mo cumulative profit
  - 📊 Price history + trend analysis per task
  - 🗑️ Remove tasks when done
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import apply_professional_theme

st.set_page_config(page_title="Product Tasks · trend-hunter", page_icon="📋", layout="wide")
apply_professional_theme()
st.title("📋 Product Tasks")
st.caption(
    "Add products to your task board — get instant profit projections and business forecasts. "
    "Max 20 products."
)

st_autorefresh(interval=30_000, key="tasks_refresh")

# ── storage ───────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


storage = _storage()

# ── session state for tasks ───────────────────────────────────────────────────
if "product_tasks" not in st.session_state:
    st.session_state.product_tasks = []

MAX_TASKS = 20

# ── adjustable markup ─────────────────────────────────────────────────────────
if "markup_pct" not in st.session_state:
    st.session_state.markup_pct = 20.0

# ── data loaders ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=20, show_spinner=False)
def _search_products(query: str, limit: int = 10):
    """Search products, deduplicated by (source, external_id)."""
    if not query or len(query) < 2:
        return []
    try:
        rows = storage.query(
            "SELECT source, external_id, title, price, url, captured_at "
            "FROM products "
            "WHERE title ILIKE ? AND price IS NOT NULL AND price > 0 "
            "ORDER BY captured_at DESC",
            (f"%{query}%",),
        )
        # Deduplicate by (source, external_id)
        seen = set()
        unique = []
        for r in rows:
            key = (r["source"], r["external_id"])
            if key not in seen:
                seen.add(key)
                unique.append(r)
                if len(unique) >= limit:
                    break
        return unique
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _load_batch_classification(external_ids: tuple):
    """Load classification for all task products in one query."""
    if not external_ids:
        return []
    try:
        placeholders = ",".join("?" for _ in external_ids)
        return storage.query(
            f"SELECT source, external_id, timeframe_days, status, "
            f"slope_pct_per_day, n_points "
            f"FROM agg_product_status "
            f"WHERE source='shopify' AND external_id IN ({placeholders})",
            list(external_ids),
        )
    except Exception:
        return []


# ── profit calculation ────────────────────────────────────────────────────────
def calc_task_profit(price: float, markup_pct: float = 20.0) -> dict:
    """Calculate profit projections. Returns dict with buy/sell/profit/daily/monthly."""
    sell_price = price * (1 + markup_pct / 100)
    profit_per_unit = sell_price - price

    return {
        "buy_price": round(price, 2),
        "sell_price": round(sell_price, 2),
        "markup_pct": markup_pct,
        "profit_per_unit": round(profit_per_unit, 2),
        "daily_10": round(profit_per_unit * 10, 2),
        "daily_50": round(profit_per_unit * 50, 2),
        "daily_100": round(profit_per_unit * 100, 2),
        "monthly_10": round(profit_per_unit * 10 * 30, 2),
        "monthly_50": round(profit_per_unit * 50 * 30, 2),
        "monthly_100": round(profit_per_unit * 100 * 30, 2),
    }


def calc_timeline(profit_per_unit: float, sales_per_day: int = 50) -> list[dict]:
    """Project cumulative profit over time at given sales volume."""
    monthly = profit_per_unit * sales_per_day * 30
    return [
        {"period": "1 Month", "cumulative_profit": round(monthly, 2), "sales": sales_per_day * 30},
        {"period": "3 Months", "cumulative_profit": round(monthly * 3, 2), "sales": sales_per_day * 90},
        {"period": "6 Months", "cumulative_profit": round(monthly * 6, 2), "sales": sales_per_day * 180},
        {"period": "12 Months", "cumulative_profit": round(monthly * 12, 2), "sales": sales_per_day * 365},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
tasks = st.session_state.product_tasks
markup = st.session_state.markup_pct

st.sidebar.header("⚙️ Settings")
markup = st.sidebar.slider(
    "💰 Markup %",
    min_value=5, max_value=200, value=int(markup), step=5,
    help="Your target profit margin on buy price",
)
st.session_state.markup_pct = markup

st.sidebar.divider()
st.sidebar.header("📋 Task Board")
st.sidebar.metric("Active Tasks", f"{len(tasks)} / {MAX_TASKS}")

if tasks:
    valid_profits = [calc_task_profit(t["price"], markup)["profit_per_unit"] for t in tasks if t.get("price", 0) > 0]
    total_investment = sum(t["price"] for t in tasks if t.get("price"))
    total_profit = sum(valid_profits)
    avg_profit = total_profit / len(valid_profits) if valid_profits else 0
    st.sidebar.metric("💰 Total Investment", f"${total_investment:,.2f}" if total_investment else "$0")
    st.sidebar.metric("💵 Est. Total Profit", f"${total_profit:,.2f}" if total_profit else "$0")
    st.sidebar.metric("📈 Avg Profit/Unit", f"${avg_profit:.2f}" if avg_profit else "$0")

if st.sidebar.button("🗑️ Clear All Tasks", width="stretch", type="secondary"):
    st.session_state.product_tasks = []
    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: SEARCH & ADD
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("🔍 Add Products")
st.caption("Search Shopify products by name. Max 20.")

search_query = st.text_input(
    "Search products",
    placeholder="Type product name (min 2 chars)...",
    key="task_search",
    label_visibility="collapsed",
)

if search_query and len(search_query) >= 2:
    results = _search_products(search_query)
    if results:
        existing_keys = {(t["source"], t["external_id"]) for t in tasks}
        for r in results:
            key = (r["source"], r["external_id"])
            if key in existing_keys:
                continue
            if len(tasks) >= MAX_TASKS:
                st.warning(f"Max {MAX_TASKS} reached. Remove some to add more.")
                break

            cols = st.columns([4, 1, 1, 1])
            with cols[0]:
                st.markdown(f"**{r['title'] or r['external_id']}**")
                st.caption(f"💰 ${float(r['price']):.2f}  |  🏪 {r['source']}")
            with cols[1]:
                profit = calc_task_profit(float(r["price"]), markup)
                st.metric("Sell at", f"${profit['sell_price']:.2f}")
            with cols[2]:
                st.metric("Profit", f"${profit['profit_per_unit']:.2f}")
            with cols[3]:
                if st.button("➕ Add", key=f"add_{r['source']}_{r['external_id']}", width="stretch"):
                    tasks.append({
                        "source": r["source"],
                        "external_id": r["external_id"],
                        "title": r["title"] or r["external_id"],
                        "price": float(r["price"]) if r.get("price") else 0,
                        "url": r.get("url") or "",
                        "added_at": datetime.now(UTC).isoformat(),
                        "note": "",
                    })
                    st.rerun()

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: COMPACT TASK TABLE
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("📋 Your Task Board")

if not tasks:
    st.info("No products added yet. Search and add products above to see projections.")
    st.stop()

# Batch load classifications for all tasks at once
task_ids = tuple(t["external_id"] for t in tasks if t.get("external_id"))
classifications = _load_batch_classification(task_ids) if task_ids else []

# Build status lookup
status_lookup = {}
for c in classifications:
    key = c["external_id"]
    if key not in status_lookup:
        status_lookup[key] = {"status": c["status"], "slope": 0.0}
    slope = float(c.get("slope_pct_per_day", 0) or 0) * 100
    if abs(slope) > abs(status_lookup[key]["slope"]):
        status_lookup[key] = {"status": c["status"], "slope": slope}

# Build compact table
table_data = []
for i, t in enumerate(tasks):
    price = t.get("price", 0)
    profit = calc_task_profit(price, markup) if price > 0 else None
    sl = status_lookup.get(t["external_id"], {})
    sl_pct = f"{sl.get('slope', 0):+.2f}%" if sl.get("slope") else "—"
    sts = sl.get("status", "—").upper() if sl else "—"
    url_link = f"[🔗]({t['url']})" if t.get("url") else "—"

    table_data.append({
        "#": i + 1,
        "Product": str(t["title"])[:35],
        "Buy": f"${price:.2f}" if price > 0 else "—",
        f"Sell ({markup:.0f}%↑)": f"${profit['sell_price']:.2f}" if profit else "—",
        "Profit": f"${profit['profit_per_unit']:.2f}" if profit else "—",
        "Trend": sl_pct,
        "Status": sts,
        "Link": url_link,
    })

st.dataframe(
    pd.DataFrame(table_data),
    width="stretch",
    hide_index=True,
    height=min(45 * len(table_data) + 45, 550),
    column_config={
        "Link": st.column_config.LinkColumn("Link", display_text="View →"),
    },
)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: SELECTED PRODUCT DEEP DIVE
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("🔍 Product Deep Dive")
selected_idx = st.selectbox(
    "Select a product to analyze",
    range(len(tasks)),
    format_func=lambda i: f"#{i+1}: {tasks[i]['title'][:50]}",
    key="task_dive",
)

t = tasks[selected_idx]
price = t.get("price", 0)

if price > 0:
    profit = calc_task_profit(price, markup)
    timeline = calc_timeline(profit["profit_per_unit"])

    with st.container(border=True):
        st.markdown(f"##### {t['title']}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("💰 Buy Price", f"${profit['buy_price']:.2f}")
        c2.metric(f"🎯 Sell ({markup:.0f}%↑)", f"${profit['sell_price']:.2f}")
        c3.metric("💵 Profit/Unit", f"${profit['profit_per_unit']:.2f}")
        c4.metric("🏪 Source", t["source"])

        # Timeline projection cards
        st.markdown("##### 🚀 Timeline Profit Projection (50 sales/day)")
        tl_cols = st.columns(4)
        for i_tl, tl in enumerate(timeline):
            tl_cols[i_tl].metric(
                tl["period"],
                f"${tl['cumulative_profit']:,.2f}",
                f"{tl['sales']} units",
            )

        # Scenario comparison
        st.markdown("##### 📊 Sales Scenario Comparison")
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("10 sales/day", f"${profit['daily_10']:.2f}/day", f"${profit['monthly_10']:.2f}/mo")
        sc2.metric("50 sales/day", f"${profit['daily_50']:.2f}/day", f"${profit['monthly_50']:.2f}/mo")
        sc3.metric("100 sales/day", f"${profit['daily_100']:.2f}/day", f"${profit['monthly_100']:.2f}/mo")

        # Note
        st.markdown("##### 📝 Notes")
        note = st.text_area("", value=t.get("note", ""), key=f"note_{selected_idx}", height=80, label_visibility="collapsed")
        if note != t.get("note", ""):
            st.session_state.product_tasks[selected_idx]["note"] = note

        # Remove
        if st.button(f"🗑️ Remove #{selected_idx + 1} from Tasks", type="secondary", width="stretch"):
            st.session_state.product_tasks.pop(selected_idx)
            st.rerun()

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: MASTER SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("📊 Master Business Projection")

valid_tasks = [t for t in tasks if t.get("price", 0) > 0]
if valid_tasks:
    profits = [calc_task_profit(t["price"], markup) for t in valid_tasks]
    total_buy = sum(t["price"] for t in valid_tasks)
    total_profit_per_unit = sum(p["profit_per_unit"] for p in profits)
    avg_profit = total_profit_per_unit / len(valid_tasks) if valid_tasks else 0

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("📦 Tracked", len(valid_tasks))
    s2.metric("💰 Total Investment", f"${total_buy:,.2f}")
    s3.metric("💵 Profit/Unit Total", f"${total_profit_per_unit:,.2f}")
    s4.metric("📈 Avg Profit/Unit", f"${avg_profit:.2f}")

    # Combined timeline
    combined_timeline = calc_timeline(total_profit_per_unit)
    st.markdown("##### 🚀 Combined Timeline (all products, 50 sales/day each)")
    tl2_cols = st.columns(4)
    for i, tl in enumerate(combined_timeline):
        tl2_cols[i].metric(tl["period"], f"${tl['cumulative_profit']:,.2f}", f"{tl['sales']} units")

    st.caption(f"Projections at {markup:.0f}% markup. Adjust markup in sidebar.")

st.divider()
st.caption(f"Updated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
