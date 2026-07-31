"""💰 Money — top-margin rows + validated-ad overlap + run_history feedback.

Ranks rows from the `money` table by `margin_pct` DESC and joins them
with curated ad evidence that has been running ≥ 30 days. A row that
(1) sits in the top-margin table AND (2) has a long-running ad in the
same niche is a *double-validated* money opportunity — highest conviction
to scaffold on Shopify next.

Plus a bottom panel that surfaces the last ``money-sweep`` /
``arbitrage`` / ``scaffold`` run from ``run_history`` so an operator
can see *when* the loop last fired — the first half of the system's
sense→decide→act→measure feedback loop.

Run via :code:`make money` (arbitrage → scaffold) to populate the table.
Run via :code:`make ads` to see what the validator reports standalone.
Run via :code:`make loop-report` to see the 7-day trend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.ads_validator import AdsValidator
from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.observe.run_ledger import last_run, recent_runs

st.set_page_config(page_title="Money · trend-hunter", page_icon="💰", layout="wide")
st.title("💰 Money")
st.caption(
    "Top-margin rows × validated ads (≥ 30 days running). "
    "Overlap is the highest-conviction set. Refreshes every 60 s.",
)

st_autorefresh(interval=60_000, key="money_refresh")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


@st.cache_resource(show_spinner=False)
def _validator() -> AdsValidator:
    return AdsValidator(get_settings().ads_path)


@st.cache_data(ttl=15, show_spinner=False)
def _money_rows() -> list[dict]:
    try:
        return _storage().query("""
            SELECT sku, retail_price, supplier_cost, shipping_cost,
                   cac_estimate, margin_pct, currency, recorded_at,
                   COALESCE(NULLIF(product_title, ''), sku) AS label
            FROM money
            ORDER BY margin_pct DESC, retail_price DESC
        """)
    except Exception:
        # money table may not exist yet on a brand-new install
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _validated_ads() -> list[dict]:
    return [
        {
            "creative_url": a.creative_url,
            "advertiser": a.advertiser,
            "niche": a.niche,
            "days_running": a.days_running,
        }
        for a in _validator().validated()
    ]


@st.cache_data(ttl=15, show_spinner=False)
def _last_runs() -> dict[str, dict | None]:
    """Latest run per command from `run_history` — the feedback loop summary."""
    storage = _storage()
    out: dict[str, dict | None] = {}
    for cmd in ("money-sweep", "arbitrage", "scaffold", "validate-ads", "run", "scan"):
        try:
            out[cmd] = last_run(storage, cmd)
        except Exception:
            out[cmd] = None
    return out


@st.cache_data(ttl=30, show_spinner=False)
def _money_sweep_7d_buckets() -> dict[str, int]:
    """Date → success-count for the sparkline (last 7 days)."""
    storage = _storage()
    try:
        rows = recent_runs(storage, days=7)
    except Exception:
        return {}
    buckets: dict[str, int] = {}
    for r in rows:
        ts = r.get("started_at")
        if ts is None or r["command"] != "money-sweep":
            continue
        d = ts.strftime("%Y-%m-%d")  # type: ignore[union-attr]
        if r["status"] == "ok":
            buckets[d] = buckets.get(d, 0) + 1
    return buckets


def _age_label(ts) -> str:  # noqa: ANN001
    if ts is None:
        return "never"
    s = int((datetime.now(UTC) - ts).total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86_400:
        return f"{s // 3600}h ago"
    return f"{s // 86_400}d ago"


def _niche_hits(title: str | None, ads_by_niche: dict[str, list[dict]]) -> list[dict]:
    """Free-text niche overlap: split product title on whitespace and check
    that any token (or 2-gram) matches the curated niche."""
    if not title:
        return []
    haystack = title.lower()
    hits: list[dict] = []
    for niche, ads in ads_by_niche.items():
        if niche in haystack:
            hits.extend(ads)
    return hits


storage = _storage()
rows = _money_rows()
ads = _validated_ads()
last_runs = _last_runs()
sparkline = _money_sweep_7d_buckets()

if not rows and not ads:
    st.warning(
        "Nothing to show yet. From your terminal: "
        ":code:`make run && make money && make ads`, then reload.",
        icon="🛰️",
    )

# ── KPI cards ────────────────────────────────────────────────────────────────
ads_by_niche: dict[str, list[dict]] = {}
for a in ads:
    ads_by_niche.setdefault(a["niche"].lower(), []).append(a)

if rows:
    rows_with_overlap = []
    for r in rows:
        hits = _niche_hits(r.get("label"), ads_by_niche)
        r["ad_overlap_count"] = len(hits)
        r["ad_overlap_niches"] = ", ".join(sorted({h["niche"] for h in hits}))
        r["ad_overlap_max_days"] = max((h["days_running"] for h in hits), default=0)
        rows_with_overlap.append(r)
    rows = rows_with_overlap

k1, k2, k3, k4 = st.columns(4)
k1.metric("Profitable rows", len(rows))
k2.metric("Validated ads (≥30d)", len(ads))
k3.metric(
    "Overlap (rows × ads)",
    sum(1 for r in rows if r.get("ad_overlap_count", 0) > 0),
)
avg_margin = round(sum(r["margin_pct"] for r in rows) / len(rows) * 100, 1) if rows else 0.0
k4.metric("Avg margin %", f"{avg_margin:.1f}%")

st.caption(f"Snapshot at {datetime.now(UTC):%Y-%m-%d %H:%M:%S UTC}")

st.divider()

# ── ranked + overlap table ───────────────────────────────────────────────────
if rows:
    st.subheader("Top-margin rows with ad-overlap signal")
    df = pd.DataFrame(rows)
    # Surface overlap to the front so it's visible after sort
    front_cols = [
        "label",
        "margin_pct",
        "retail_price",
        "supplier_cost",
        "ad_overlap_count",
        "ad_overlap_niches",
        "ad_overlap_max_days",
        "sku",
        "currency",
        "recorded_at",
    ]
    df = df[[c for c in front_cols if c in df.columns]]
    df["margin_pct"] = (df["margin_pct"] * 100).round(1)

    gb = GridOptionsBuilder.from_dataframe(df)  # type: ignore[arg-type]
    gb.configure_side_bar()
    gb.configure_default_column(filter=True, sortable=True, resizable=True)
    gb.configure_pagination(paginationAutoPageSize=False)
    gb.configure_column(
        "ad_overlap_count",
        headerName="Ad hits",
        cellStyle=({"function": "params.value > 0 ? {'backgroundColor': '#1b4332'} : None"}),
    )

    AgGrid(
        df,
        gridOptions=gb.build(),
        theme="streamlit",
        height=420,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True,
    )

    st.caption(
        "Sort by `Ad hits` to surface rows whose niche has an ad running "
        "≥ 30 days — that's the double-validated money set.",
    )

    st.divider()
    st.subheader("🎯 Double-validated highlights")
    highlighted = [r for r in rows if r.get("ad_overlap_count", 0) > 0]
    if highlighted:
        for r in highlighted:
            with st.container(border=True):
                col_title, col_meta = st.columns([3, 1])
                with col_title:
                    st.markdown(f"#### `{r['label']}`")
                    st.write(
                        f"**Margin:** {r['margin_pct'] * 100:.1f}%  ·  "
                        f"**Retail:** ${r['retail_price']:.2f}  ·  "
                        f"**COGS:** ${r['supplier_cost']:.2f} + "
                        f"${r['shipping_cost']:.2f} ship + "
                        f"${r['cac_estimate']:.2f} CAC",
                    )
                with col_meta:
                    st.metric(
                        "Ad overlap",
                        f"{r['ad_overlap_count']} niche(s)",
                        delta=f"max {r['ad_overlap_max_days']}d running",
                        delta_color="normal",
                    )
    else:
        st.info(
            "No overlap yet between `money` rows and your curated ads. "
            "Either widen `data/suppliers.csv` to cover the same niches "
            "you're advertising, or add new ad entries to `data/ads.json`.",
        )

st.divider()

# ── raw validated ads ───────────────────────────────────────────────────────
if ads:
    st.subheader("Validated ads (≥ 30 days running)")
    df_ads = pd.DataFrame(ads)
    gb = GridOptionsBuilder.from_dataframe(df_ads)  # type: ignore[arg-type]
    gb.configure_side_bar()
    gb.configure_default_column(filter=True, sortable=True, resizable=True)
    AgGrid(
        df_ads,
        gridOptions=gb.build(),
        theme="streamlit",
        height=260,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True,
    )
else:
    st.info(
        f"`{Path(get_settings().ads_path).as_posix()}` is empty or "
        "missing. Add curated rows with `days_running ≥ 30` to populate "
        "this list.",
    )

# ── run_history feedback (loop engineering spine) ───────────────────────────
st.divider()
st.subheader("🪞 Loop-engineering feedback (from `run_history`)")
st.caption(
    "When the system last fired each leg of the money loop — the first "
    "half of the sense→decide→act→measure loop. Refreshes every 60 s "
    "to surface dead components in real time.",
)

if last_runs:
    last_cols = st.columns(min(3, len(last_runs)))
    for col, (cmd, rec) in zip(last_cols, last_runs.items(), strict=False):
        if rec is None:
            col.metric(label=cmd, value="never", delta="—")
            continue
        status = rec.get("status") or "pending"
        icon = "✅" if status == "ok" else "❌" if status == "err" else "⏳"
        col.metric(
            label=f"{icon} {cmd}",
            value=_age_label(rec.get("started_at")),
            delta=(f"{rec.get('duration_ms', 0)}ms" if rec.get("duration_ms") else "—"),
            delta_color="off",
        )

# 7-day money-sweep success sparkline
if sparkline:
    st.markdown("**money-sweep success counts (last 7 days)**")
    df_spark = pd.DataFrame(
        [{"day": k, "ok_runs": v} for k, v in sorted(sparkline.items())],
    )
    df_spark = df_spark.set_index("day")
    st.bar_chart(df_spark, height=160)
else:
    st.info(
        "No money-sweep runs recorded yet. From your terminal: "
        ":code:`make money-sweep` (or `make money`) to populate.",
        icon="🛰️",
    )

with st.expander("What's a feedback loop?"):
    st.markdown(
        """
        The **money loop** is the system's first feedback loop:

        > **sense** (scan) → **decide** (classify) → **act** (arbitrage →
        > scaffold) → **measure** (money table → dollars on track).

        Each step writes a row to ``run_history`` with start/end/status/
        counters so operator glances at the dashboard's Money page can
        see *when* each loop leg last fired. If a leg is stale, the
        doctor surfaces it; if a leg errored, the loop-report CLI shows
        the error type in plain English.
        """
    )
