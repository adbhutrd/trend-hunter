"""🏠 trend-hunter — Find trending products. Make money. Simple."""

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


_SYSTEMD_SERVICE_NAME = "buffy-scheduler"


def _is_systemd_service_running() -> bool:
    """Check if the systemd user service is active — if so, don't spawn a duplicate."""
    try:
        ret = subprocess.run(
            ["systemctl", "--user", "--quiet", "is-active", _SYSTEMD_SERVICE_NAME],
            capture_output=True,
            timeout=3,
        )
        return ret.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


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

    # If the systemd service is active, rely on it — don't spawn a duplicate.
    if _is_systemd_service_running():
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
    # First check if the systemd service is running (preferred).
    if _is_systemd_service_running():
        return True, None
    # Fallback: check legacy PID file from subprocess-spawned scheduler.
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
# HERO — Big title, one-liner, system status
# ══════════════════════════════════════════════════════════════════════════════
col_logo, col_title, col_status = st.columns([1, 3, 2])
with col_logo:
    st.markdown("# 🛰️")
with col_title:
    st.markdown("## **trend-hunter**")
    st.markdown("*Find trending products before everyone else*")
with col_status:
    scheduler_running, scheduler_pid = _scheduler_status()
    if scheduler_running:
        st.success("✅ System running — auto-scan every 30 min", icon="🟢")
    else:
        st.warning("⚠️ Scanner not running", icon="🟡")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# BIG NUMBERS — Just the essentials
# ══════════════════════════════════════════════════════════════════════════════
storage = _storage()

try:
    total_prod = storage.query("SELECT count(*) as cnt FROM products")[0]["cnt"]
    total_price = storage.query("SELECT count(*) as cnt FROM products WHERE price > 0 AND price IS NOT NULL")[0]["cnt"]
    avg_price = storage.query("SELECT avg(price) as avg_p FROM products WHERE price > 0 AND price IS NOT NULL")[0]["avg_p"] or 0

    ml_total = 0
    ml_rising = 0
    ml_declining = 0
    try:
        ml_total = storage.query("SELECT count(*) as cnt FROM ml_predictions")[0]["cnt"]
        ml_rising = storage.query("SELECT count(*) as cnt FROM ml_predictions WHERE predicted_status='rising' OR predicted_status='breakout'")[0]["cnt"]
        ml_declining = storage.query("SELECT count(*) as cnt FROM ml_predictions WHERE predicted_status='declining'")[0]["cnt"]
    except Exception:
        pass

    rising_count = 0
    declining_count = 0
    try:
        statuses = storage.query("SELECT status, count(*) as n FROM agg_product_status WHERE status IN ('rising','declining') GROUP BY status")
        for r in statuses:
            if r["status"] == "rising":
                rising_count = r["n"]
            elif r["status"] == "declining":
                declining_count = r["n"]
    except Exception:
        pass

    sources = storage.query(
        "SELECT source, count(DISTINCT external_id) as products, "
        "round(avg(price), 2) as avg_p FROM products "
        "WHERE price > 0 GROUP BY source ORDER BY products DESC"
    )
    num_sources = len(sources) if sources else 0

    # ── 4 big metric cards ──────────────────────────────────────────────────
    row1 = st.columns(4)
    row1[0].metric("📦 **Products Tracked**", f"{total_prod:,}", help="Total products found across all stores")
    row1[1].metric("🏪 **Stores Scanned**", f"{num_sources}", help="Active data sources (Shopify, GitHub, Google Trends, HackerNews)")
    row1[2].metric("💰 **Avg Price**", f"${avg_price:.2f}", help="Average price of all products with prices")
    row1[3].metric("🔮 **AI Forecasts**", f"{ml_total:,}", help="Products analyzed by the AI prediction engine")

    # ── 4 status cards ──────────────────────────────────────────────────────
    row2 = st.columns(4)
    row2[0].metric("📈 **Rising**", f"{rising_count}", f"AI predicts +{ml_rising} more", help="Products currently trending up")
    row2[1].metric("📉 **Declining**", f"{declining_count}", f"AI predicts -{ml_declining} more", help="Products currently trending down")
    row2[2].metric("🛍️ **With Prices**", f"{total_price:,}", help="Products that have price data")
    row2[3].metric("🔬 **Patterns Found**", f"{rising_count + declining_count}", help="Products showing clear trend patterns")

except Exception:
    st.info("⏳ Data is building up. First scan may take a few minutes...")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# WHAT TO DO — Quick action guide
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("### 🎯 What do you want to do?")

col_a, col_b, col_c, col_d = st.columns(4)

with col_a:
    st.markdown("""
    <div class="nav-card" style="text-align:center;padding:1.5rem 1rem;">
        <div style="font-size:2.5rem;margin-bottom:0.5rem;">🔥</div>
        <div style="font-size:1rem;font-weight:700;margin-bottom:0.3rem;">What's Hot Now</div>
        <div style="font-size:0.8rem;color:#94a3b8;">Products trending right now →</div>
    </div>
    """, unsafe_allow_html=True)

with col_b:
    st.markdown("""
    <div class="nav-card" style="text-align:center;padding:1.5rem 1rem;">
        <div style="font-size:2.5rem;margin-bottom:0.5rem;">🔮</div>
        <div style="font-size:1rem;font-weight:700;margin-bottom:0.3rem;">AI Predictions</div>
        <div style="font-size:0.8rem;color:#94a3b8;">What will rise or fall next →</div>
    </div>
    """, unsafe_allow_html=True)

with col_c:
    st.markdown("""
    <div class="nav-card" style="text-align:center;padding:1.5rem 1rem;">
        <div style="font-size:2.5rem;margin-bottom:0.5rem;">🛒</div>
        <div style="font-size:1rem;font-weight:700;margin-bottom:0.3rem;">Find Products to Buy</div>
        <div style="font-size:0.8rem;color:#94a3b8;">All products with buy links →</div>
    </div>
    """, unsafe_allow_html=True)

with col_d:
    st.markdown("""
    <div class="nav-card" style="text-align:center;padding:1.5rem 1rem;">
        <div style="font-size:2.5rem;margin-bottom:0.5rem;">📡</div>
        <div style="font-size:1rem;font-weight:700;margin-bottom:0.3rem;">Live Monitor</div>
        <div style="font-size:0.8rem;color:#94a3b8;">Alerts & daily reports →</div>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SOURCE BREAKDOWN — Which stores are feeding data
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("### 📡 Data Sources")

try:
    if sources:
        src_cols = st.columns(min(len(sources), 5))
        for i, src in enumerate(sources):
            icon = {"shopify": "🛍️", "github": "🐙", "googletrends": "📊", "hackernews": "🗞️", "ebay": "🏷️"}.get(
                src["source"], "📦"
            )
            src_cols[i].metric(
                f"{icon} {src['source'].title()}",
                f"{src['products']} products",
                f"${src['avg_p']:.2f} avg",
                help=f"Products found from {src['source']}",
            )
except Exception:
    pass

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM HEALTH — Simple status check
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("### 🩺 System Status")
try:
    health = storage.query("SELECT source, state FROM health ORDER BY source")
    if health:
        hcols = st.columns(min(len(health), 5))
        for i, h in enumerate(health):
            icon = {"ok": "✅", "stale": "⚠️", "failing": "❌", "dead": "💀"}.get(h["state"], "❓")
            color = {"ok": "#22c55e", "stale": "#f59e0b", "failing": "#ef4444", "dead": "#ef4444"}.get(h["state"], "#94a3b8")
            hcols[i].markdown(
                f"""<div style="background:#150d24;border:1px solid #2a1f3b;border-radius:10px;padding:0.75rem;text-align:center;">
                    <div style="font-size:1.5rem;">{icon}</div>
                    <div style="font-weight:600;font-size:0.85rem;color:#e2e8f0;">{h['source'].title()}</div>
                    <div style="font-size:0.75rem;color:{color};font-weight:600;">{h['state'].upper()}</div>
                </div>""",
                unsafe_allow_html=True,
            )
except Exception:
    pass

st.caption(
    f"Last refreshed: {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC · "
    f"Data updates every 30 minutes automatically"
)
