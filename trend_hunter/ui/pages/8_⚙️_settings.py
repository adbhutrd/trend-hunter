"""⚙️ Settings — manage sources.json + env, run doctor live, edit intervals."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import streamlit as st

from trend_hunter.core.config import get_settings

st.set_page_config(page_title="Settings · trend-hunter", page_icon="⚙️", layout="wide")
st.title("⚙️ Settings")
st.caption("Edit `sources.json`, run `doctor`, view effective configuration.")


# ── effective config (read-only display) ─────────────────────────────────────
settings = get_settings()
st.subheader("Effective configuration")
st.json(
    {
        "db_path": str(settings.db_path),
        "log_level": settings.log_level,
        "log_dir": str(settings.log_dir),
        "scan_interval_minutes": settings.scan_interval_minutes,
        "aggregate_hour": settings.aggregate_hour,
        "dash_port": settings.dash_port,
        "discord_webhook": "(set)" if settings.discord_webhook else None,
        "telegram_bot_token": "(set)" if settings.telegram_bot_token else None,
        "gmail_address": "(set)" if settings.gmail_address else None,
    },
)


st.divider()


# ── sources.json editor ──────────────────────────────────────────────────────
st.subheader("sources.json")
src_path = Path("./sources.json")
if not src_path.exists():
    st.error(f"{src_path} missing — please re-create from the repo skeleton.")
    st.stop()

current = src_path.read_text()
edited = st.text_area(
    "Edit sources.json — write back with the button below.",
    value=current,
    height=400,
    key="sources_editor",
)

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("💾 Save sources.json"):
        # Validate JSON before write
        try:
            parsed = json.loads(edited)
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON: {e}")
        else:
            src_path.write_text(edited)
            st.success(f"Saved {src_path}")
            try:
                st.cache_data.clear()
            except Exception:
                pass
with col2:
    if st.button("⚖️  Validate only"):
        try:
            json.loads(edited)
            st.success("Valid JSON ✅")
        except json.JSONDecodeError as e:
            st.error(f"Invalid: {e}")
with col3:
    st.download_button(
        "⬇️ Download current sources.json",
        data=current,
        file_name="sources.json",
        mime="application/json",
    )


st.divider()


# ── live `doctor` control ────────────────────────────────────────────────────
st.subheader("Run `make doctor` here")
if st.button("🩺 Run doctor now"):
    with st.spinner("running self-test…"):
        result = subprocess.run(                                # noqa: S603
            [sys.executable, "-m", "trend_hunter.scripts.doctor"],
            capture_output=True,
            text=True,
            check=False,
        )
    st.code(result.stdout or "(no stdout)", language="bash")
    if result.stderr:
        st.code(result.stderr, language="bash")
    st.write(f"exit code = `{result.returncode}`")


# ── env dump ─────────────────────────────────────────────────────────────────
with st.expander("Environment (TH_-prefixed only)"):
    import os
    env = {k: v for k, v in os.environ.items() if k.startswith("TH_")}
    st.json(env)
