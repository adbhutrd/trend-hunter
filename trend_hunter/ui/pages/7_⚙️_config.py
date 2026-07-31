"""⚙️ CONFIG — Sources manager, scan interval, API keys, system health.

The control center:
  - 📂 Sources Manager — view/add/edit/disable data sources
  - ⏱️ Scan Interval — configure how often the pipeline runs
  - 🔐 API Keys — manage external service credentials
  - 🩺 System Health — test all connections and run diagnostics
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.ui.theme import apply_professional_theme

st.set_page_config(page_title="Config · trend-hunter", page_icon="⚙️", layout="wide")
apply_professional_theme()
st.title("⚙️ Configuration")
st.caption("Manage sources, intervals, credentials, and run system health checks.")

st_autorefresh(interval=120_000, key="config_refresh")

# ── helpers ───────────────────────────────────────────────────────────────────
SOURCES_PATH = Path("sources.json")


def _load_sources() -> dict:
    if SOURCES_PATH.exists():
        return json.loads(SOURCES_PATH.read_text())
    return {"sources": []}


def _save_sources(data: dict) -> None:
    SOURCES_PATH.write_text(json.dumps(data, indent=2))
    st.success("sources.json saved!")


@st.cache_resource(show_spinner=False)
def _storage() -> DuckDBStorage:
    return DuckDBStorage(get_settings().db_path, read_only=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: 📂 SOURCES MANAGER
# ═══════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs(["📂 Sources", "⏱️ Schedule", "🔐 API Keys", "🩺 Health"])

with tab1:
    st.subheader("📂 Data Sources")
    st.caption("Currently configured sources — enable/disable or add new ones.")

    try:
        sources_config = _load_sources()
        sources_list = sources_config.get("sources", [])

        if not sources_list:
            st.info("No sources configured in sources.json yet.")
        else:
            for i, src in enumerate(sources_list):
                name = src.get("name", src.get("type", f"source_{i}"))
                enabled = src.get("enabled", True)
                src_type = src.get("type", "unknown")
                url = src.get("url", "")

                with st.container(border=True):
                    col1, col2, col3, col4 = st.columns([3, 2, 3, 1])
                    with col1:
                        st.markdown(f"**{name}**")
                        st.caption(f"Type: `{src_type}`")
                    with col2:
                        st.caption(f"Status: {'✅ Enabled' if enabled else '❌ Disabled'}")
                    with col3:
                        if url:
                            st.caption(url[:60])
                        else:
                            st.caption("No URL configured")

                    # Edit/disable per source
                    with col4:
                        if st.button("❌", key=f"toggle_{i}", help="Toggle on/off"):
                            sources_list[i]["enabled"] = not enabled
                            _save_sources({"sources": sources_list})
                            st.rerun()

        # Add new source
        st.divider()
        st.subheader("➕ Add New Source")
        with st.form("add_source_form"):
            cols = st.columns(4)
            with cols[0]:
                new_name = st.text_input("Name", placeholder="my-shop")
            with cols[1]:
                new_type = st.selectbox("Type", ["shopify", "hackernews", "github", "googletrends"])
            with cols[2]:
                new_url = st.text_input("URL/Endpoint", placeholder="https://...")
            with cols[3]:
                new_key = st.text_input("API Key (optional)", placeholder="sk-...", type="password")

            if st.form_submit_button("➕ Add Source"):
                new_src = {
                    "name": new_name or f"source_{len(sources_list)}",
                    "type": new_type,
                    "enabled": True,
                }
                if new_url:
                    new_src["url"] = new_url
                if new_key:
                    new_src["api_key"] = new_key
                sources_list.append(new_src)
                _save_sources({"sources": sources_list})
                st.rerun()

    except Exception as e:
        st.error(f"Error loading sources.json: {e}")

with tab2:
    st.subheader("⏱️ Scan Schedule")
    st.caption("Configure how often the pipeline runs automatically.")

    settings = get_settings()
    current_interval = settings.scan_interval_minutes

    col1, col2 = st.columns([2, 2])
    with col1:
        new_interval = st.slider(
            "Scan interval (minutes)",
            min_value=5,
            max_value=240,
            value=current_interval if 5 <= current_interval <= 240 else 15,
            step=5,
            help="How often the scheduler runs scan + classify + aggregate",
        )

        if st.button("⏱️ Update Interval"):
            # Update env for current session
            os.environ["TH_SCAN_INTERVAL_MINUTES"] = str(new_interval)
            st.success(f"Interval set to {new_interval} minutes. Restart dashboard to apply.")

    with col2:
        st.metric("Current Interval", f"{current_interval} min")
        st.caption("Next scan will run automatically.")
        st.code(
            "# To persist, set in .env:\n"
            "TH_SCAN_INTERVAL_MINUTES=15",
            language="bash",
        )

    st.divider()
    st.subheader("🔄 Manual Controls")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if st.button("▶️ Run Scan Now", width="stretch"):
            with st.spinner("Scanning..."):
                result = subprocess.run(
                    [sys.executable, "-m", "trend_hunter.cli", "run"],
                    capture_output=True, text=True, timeout=300,
                    cwd=Path.cwd(),
                )
                if result.returncode == 0:
                    st.success("Scan completed successfully! Refreshing data...")
                    st.cache_data.clear()
                else:
                    st.error(f"Scan failed: {result.stderr[:500]}")
                with st.expander("See output"):
                    st.code(result.stdout[-1000:] if result.stdout else "No output")

    with col_b:
        if st.button("🔁 Force Re-classify", width="stretch"):
            with st.spinner("Re-classifying..."):
                result = subprocess.run(
                    [sys.executable, "-m", "trend_hunter.cli", "aggregate"],
                    capture_output=True, text=True, timeout=120,
                    cwd=Path.cwd(),
                )
                if result.returncode == 0:
                    st.success("Re-classification complete!")
                    st.cache_data.clear()
                else:
                    st.error(f"Failed: {result.stderr[:500]}")

    with col_c:
        if st.button("🩺 Run Doctor", width="stretch"):
            with st.spinner("Running diagnostics..."):
                result = subprocess.run(
                    [sys.executable, "-m", "trend_hunter.cli", "doctor"],
                    capture_output=True, text=True, timeout=30,
                    cwd=Path.cwd(),
                )
                if result.returncode == 0:
                    st.success("All checks passed ✅")
                else:
                    st.error(f"Issues found: {result.stdout[:500]}")
                with st.expander("Full output"):
                    st.code(result.stdout)

with tab3:
    st.subheader("🔐 API Keys & Credentials")
    st.caption("Manage API keys for external services. These are read from environment variables.")

    # Known env vars for the project
    known_env_vars = [
        ("TH_SHOPIFY_DOMAIN", "Shopify store domain"),
        ("TH_SHOPIFY_TOKEN", "Shopify admin API token"),
        ("TH_DISCORD_WEBHOOK", "Discord webhook URL for alerts"),
        ("TH_TELEGRAM_BOT_TOKEN", "Telegram bot token"),
        ("TH_TELEGRAM_CHAT_ID", "Telegram chat ID for alerts"),
        ("TH_SMTP_HOST", "SMTP server for email alerts"),
        ("TH_SMTP_PORT", "SMTP port"),
        ("TH_SMTP_USERNAME", "SMTP username"),
        ("TH_SMTP_PASSWORD", "SMTP password"),
        ("TH_FROM_EMAIL", "From email address"),
        ("TH_TO_EMAIL", "To email address"),
    ]

    env_data = []
    for var_name, description in known_env_vars:
        value = os.environ.get(var_name, "")
        # Mask sensitive values
        display = value[:8] + "..." + value[-4:] if value and len(value) > 15 else (value if value else "—")
        env_data.append({
            "Variable": var_name,
            "Value": display,
            "Description": description,
            "Status": "✅ Set" if value else "⬜ Not Set",
        })

    st.dataframe(
        env_data,
        width="stretch",
        hide_index=True,
        column_config={
            "Value": st.column_config.TextColumn("Value", width="medium"),
        },
    )

    st.caption("Set these in your `.env` file or export them in your shell profile.")

with tab4:
    st.subheader("🩺 System Health")
    st.caption("Run diagnostics to verify everything is working.")

    # Quick health checks
    checks = []

    # DB check
    try:
        s = _storage()
        r = s.query("SELECT count(*) as n FROM products")
        checks.append({"Check": "📦 Database Connection", "Status": "✅ OK", "Detail": f"{r[0]['n']} products"})
    except Exception as e:
        checks.append({"Check": "📦 Database Connection", "Status": "❌ FAIL", "Detail": str(e)})

    # Sources file
    if SOURCES_PATH.exists():
        try:
            srcs = json.loads(SOURCES_PATH.read_text())
            n_enabled = sum(1 for s in srcs.get("sources", []) if s.get("enabled", True))
            checks.append({"Check": "📂 Sources Config", "Status": "✅ OK", "Detail": f"{n_enabled} enabled sources"})
        except Exception as e:
            checks.append({"Check": "📂 Sources Config", "Status": "❌ FAIL", "Detail": str(e)})
    else:
        checks.append({"Check": "📂 Sources Config", "Status": "⚠️ Missing", "Detail": "sources.json not found"})

    # Classified data
    try:
        r = s.query("SELECT count(*) as n FROM agg_product_status")
        checks.append({"Check": "📊 Classified Data", "Status": "✅ OK" if r[0]["n"] > 0 else "⚠️ Empty", "Detail": f"{r[0]['n']} classified rows"})
    except Exception as e:
        checks.append({"Check": "📊 Classified Data", "Status": "❌ FAIL", "Detail": str(e)})

    # Scheduler check
    pid_file = Path("data/scheduler.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            checks.append({"Check": "🤖 Scheduler", "Status": "✅ Running", "Detail": f"PID {pid}"})
        except (OSError, ValueError):
            checks.append({"Check": "🤖 Scheduler", "Status": "❌ Not Running", "Detail": "PID file stale"})
    else:
        checks.append({"Check": "🤖 Scheduler", "Status": "⚠️ Not Running", "Detail": "No PID file (starts with dashboard)"})

    # Free disk
    try:
        stat = os.statvfs(".")
        free_gb = stat.f_frsize * stat.f_bavail / 1024 / 1024 / 1024
        checks.append({"Check": "💾 Disk Space", "Status": "✅ OK" if free_gb > 1 else "⚠️ Low", "Detail": f"{free_gb:.1f} GB free"})
    except Exception:
        pass

    st.dataframe(
        pd.DataFrame(checks),
        width="stretch",
        hide_index=True,
    )

    # Run full doctor
    st.divider()
    if st.button("🩺 Run Full System Doctor", width="stretch"):
        with st.spinner("Running comprehensive diagnostics..."):
            result = subprocess.run(
                [sys.executable, "-m", "trend_hunter.cli", "doctor"],
                capture_output=True, text=True, timeout=60,
                cwd=Path.cwd(),
            )
            with st.expander("📋 Doctor Output", expanded=True):
                st.code(result.stdout if result.stdout else "No output")
                if result.stderr:
                    st.code(f"Errors:\n{result.stderr}", language="bash")
            if result.returncode == 0:
                st.success("✅ All systems healthy!")
            else:
                st.warning("⚠️ Some checks need attention. See output above.")

# ── FOOTER ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(f"trend-hunter v0.1.0 · {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")
