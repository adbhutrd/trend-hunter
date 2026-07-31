"""🏠 trend-hunter — Professional Shopify Management Dashboard."""

from __future__ import annotations

import atexit
import fcntl
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.core.logging import configure
from trend_hunter.ui.theme import apply_professional_theme

# ── scheduler auto-start ─────────────────────────────────────────────────────
SCHEDULER_PID_FILE = Path("data/scheduler.pid")
SCHEDULER_LOCK_FILE = Path("data/scheduler.lock")


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _cleanup_scheduler(pid: int, log_fh) -> None:
    try:
        if _is_process_alive(pid):
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        log_fh.close()
    except OSError:
        pass
    for path in (SCHEDULER_PID_FILE, SCHEDULER_LOCK_FILE):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _start_backend_scheduler() -> subprocess.Popen | None:
    s = get_settings()
    if s.scan_interval_minutes <= 0:
        return None
    SCHEDULER_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(SCHEDULER_LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if SCHEDULER_PID_FILE.exists():
            try:
                existing_pid = int(SCHEDULER_PID_FILE.read_text().strip())
                if existing_pid and _is_process_alive(existing_pid):
                    return None
            except ValueError:
                pass
            SCHEDULER_PID_FILE.unlink(missing_ok=True)
        log_path = Path(s.log_dir) / "scheduler.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "trend_hunter.flows.daily"],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            st.error(f"Failed to start backend scheduler: {exc}")
            log_fh.close()
            return None
        SCHEDULER_PID_FILE.write_text(str(proc.pid))
        atexit.register(_cleanup_scheduler, proc.pid, log_fh)
        return proc
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


@st.cache_resource(show_spinner=False)
def _boot_scheduler() -> None:
    _start_backend_scheduler()


_boot_scheduler()


def _scheduler_status() -> tuple[bool, int | None]:
    if not SCHEDULER_PID_FILE.exists():
        return False, None
    try:
        pid = int(SCHEDULER_PID_FILE.read_text().strip())
        return _is_process_alive(pid), pid
    except (ValueError, OSError):
        return False, None


st.set_page_config(
    page_title="trend-hunter",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_professional_theme()


def _get_storage() -> DuckDBStorage:
    s = get_settings()
    configure(s.log_dir, s.log_level)
    return DuckDBStorage(s.db_path, read_only=True)


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return _get_storage()





# ══════════════════════════════════════════════════════════════════════════════
# HERO SECTION
# ══════════════════════════════════════════════════════════════════════════════
col_logo, col_status = st.columns([1, 4])
with col_logo:
    st.markdown("# 🛰️")
with col_status:
    st.markdown("## **trend-hunter**")
    st.caption(f"Shopify Profit Intelligence · {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")

scheduler_running, scheduler_pid = _scheduler_status()
if scheduler_running:
    st.success(f"🤖 Scheduler running (PID {scheduler_pid}) — auto-scan every 15 min", icon="✅")
else:
    st.warning("🤖 Scheduler not running", icon="⚠️")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# METRIC STRIP
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("📊 Live Overview")
storage = _storage()

try:
    total_prod = storage.query("SELECT count(*) as cnt FROM products")[0]["cnt"]
    total_class = storage.query("SELECT count(*) as cnt FROM agg_product_status")[0]["cnt"]
    total_hist = storage.query("SELECT count(*) as cnt FROM trend_history")[0]["cnt"]
    total_price = storage.query("SELECT count(*) as cnt FROM products WHERE price > 0 AND price IS NOT NULL")[0]["cnt"]
    avg_price = storage.query("SELECT avg(price) as avg_p FROM products WHERE price > 0 AND price IS NOT NULL")[0]["avg_p"] or 0
    total_patterns = 0
    try:
        total_patterns = storage.query("SELECT count(*) as cnt FROM cross_timeframe_patterns")[0]["cnt"]
    except Exception:
        pass

    # ML predictions count
    ml_total = 0
    ml_breakouts = 0
    ml_rising = 0
    try:
        ml_total = storage.query("SELECT count(*) as cnt FROM ml_predictions")[0]["cnt"]
        ml_breakouts = storage.query("SELECT count(*) as cnt FROM ml_predictions WHERE predicted_status='breakout'")[0]["cnt"]
        ml_rising = storage.query("SELECT count(*) as cnt FROM ml_predictions WHERE predicted_status='rising'")[0]["cnt"]
    except Exception:
        pass

    # Metric strip — 7 cards
    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    m1.metric("📦 Products", f"{total_prod:,}")
    m2.metric("📊 Classified", f"{total_class:,}")
    m3.metric("📈 History", f"{total_hist:,}")
    m4.metric("💲 Avg Price", f"${avg_price:.2f}")
    m5.metric("🔍 Patterns", f"{total_patterns:,}")
    m6.metric("🔮 ML Preds", f"{ml_total:,}")
    m7.metric("🚀 Breakouts", f"{ml_breakouts:,}", f"🚀 {ml_rising:,} rising")

    # Source breakdown cards
    sources = storage.query(
        "SELECT source, count(DISTINCT external_id) as products, "
        "round(avg(price), 2) as avg_p, max(captured_at) as last_scan "
        "FROM products WHERE price > 0 GROUP BY source ORDER BY products DESC"
    )
    if sources:
        st.markdown("##### 🏪 Store Sources")
        src_cols = st.columns(min(len(sources), 7))
        for i, src in enumerate(sources):
            time_str = src["last_scan"].strftime("%H:%M UTC") if hasattr(src["last_scan"], "strftime") else "?"
            src_cols[i].metric(
                f"🏪 {src['source'].title()}",
                f"{src['products']} products",
                f"${src['avg_p']:.2f} avg · {time_str}",
            )

except Exception:
    st.info("Data building up...")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# NAVIGATION GUIDE — Professional Card Grid
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("🗺️ Navigation")

nav_items = [
    ("🏠 Dashboard", "Live metrics, Top 10 ranked products, price charts, and recent alerts", "1"),
    ("🔥 Hot Now", "Momentum leaders, breakout watch, accelerating products, new finds", "2"),
    ("📈 Analytics", "Category explorer, cross-timeframe analysis, per-product deep dives", "3"),
    ("💰 P&L", "Profit estimation, ROI calculator, margin analysis, risk scores", "4"),
    ("🛒 Buy", "All products with buy links, vendor directory, price comparison", "5"),
    ("📡 Monitor", "Real-time alerts, daily/weekly trend reports, pattern changes", "6"),
    ("⚙️ Config", "Sources, schedule, API keys, system health diagnostics", "7"),
    ("📋 Tasks", "Add & track up to 20 products with instant profit projections", "8"),
    ("🔮 ML", "AI-powered predictions — Prophet forecasts for 7/14/30 days", "9"),
]

rows_nav = [nav_items[i:i+4] for i in range(0, len(nav_items), 4)]
for row_group in rows_nav:
    cols = st.columns(4)
    for col, (title, desc, _) in zip(cols, row_group, strict=False):
        with col:
            st.markdown(
                f"""<div class="nav-card">
                    <div style="font-size:1.2rem;font-weight:600;margin-bottom:6px;">{title}</div>
                    <div style="font-size:0.85rem;color:#6b7280;">{desc}</div>
                </div>""",
                unsafe_allow_html=True,
            )

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# QUICK STATS
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("🩺 System Health")
try:
    health = storage.query("SELECT source, state FROM health ORDER BY source")
    if health:
        hcols = st.columns(min(len(health), 4))
        for i, h in enumerate(health):
            icon = {"ok": "✅", "stale": "⚠️", "failing": "❌", "dead": "💀"}.get(h["state"], "❓")
            hcols[i].metric(f"{icon} {h['source']}", h["state"].upper())
except Exception:
    pass

st.caption(
    f"trend-hunter v0.1.0 · DB: `{get_settings().db_path}` · "
    f"Refresh: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC"
)
