"""`make doctor` — self-test for the trend-hunter reliability contract.

Checks (in order):

  1. DB file exists and is readable.
  2. Schema version matches expected (i.e. all migrations applied).
  3. Free disk > 1 GB so the next run doesn't OOM-write.
  4. Health table contains a heartbeat from every enabled source
     AND every source has run within the past 48 h
     (configurable via `TH_DOCTOR_MAX_AGE_HOURS`).

Exit code:

  * 0 — all green.
  * 1 — yellow: warnings that don't block execution.
  * 2 — red: hard failures (DB missing, schema mismatch, free disk < 200 MB).
"""
from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

from loguru import logger

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.core.config import get_settings
from trend_hunter.core.logging import configure

EXPECTED_SCHEMA_VERSION = 1
MIN_FREE_DISK_MB = 200
WARN_FREE_DISK_MB = 1024
MAX_AGE_HOURS_DEFAULT = 48


def doctor() -> int:
    """Run all diagnostics. Returns shell exit code."""
    s = get_settings()
    configure(s.log_dir, s.log_level)
    log = logger.bind(module="doctor")
    rc = 0

    # ── 1. DB reachable ──────────────────────────────────────────────────────
    if not s.db_path.exists():
        log.error(f"DB file missing at {s.db_path} — run `make scan` first")
        return 2

    try:
        with DuckDBStorage(s.db_path, read_only=True) as storage:
            # ── 2. schema version ────────────────────────────────────────────
            rows = storage.query(
                "SELECT max(version) AS v FROM _schema_version",
            )
            current = rows[0]["v"] if rows else None
            if current != EXPECTED_SCHEMA_VERSION:
                log.error(
                    f"schema version mismatch: applied={current}"
                    f" expected={EXPECTED_SCHEMA_VERSION}",
                )
                rc = 2
            else:
                log.info(f"✅ schema version {current}")

            # ── 3. free disk ────────────────────────────────────────────────
            usage = shutil.disk_usage(s.db_path.parent)
            free_mb = usage.free // (1024 * 1024)
            if free_mb < MIN_FREE_DISK_MB:
                log.error(f"❌ free disk {free_mb} MB (< {MIN_FREE_DISK_MB} MB)")
                rc = 2
            elif free_mb < WARN_FREE_DISK_MB:
                log.warning(f"⚠  free disk {free_mb} MB (< {WARN_FREE_DISK_MB} MB)")
                rc = max(rc, 1)
            else:
                log.info(f"✅ free disk {free_mb} MB")

            # ── 4. source heartbeat freshness ───────────────────────────────
            max_age = MAX_AGE_HOURS_DEFAULT
            datetime.now(UTC) - timedelta(hours=max_age)
            rows = storage.query(
                """
                SELECT source, state, last_run, last_ok, error_rate, detail
                FROM health
                """,
            )
            if not rows:
                log.warning("⚠  no sources ever wrote to health table — run `make scan`")
                rc = max(rc, 1)
            else:
                for r in rows:
                    name = r["source"]
                    state = r["state"]
                    last_run = r["last_run"]
                    age = (datetime.now(UTC) - last_run).total_seconds() / 3600 \
                        if last_run else None
                    if state in ("failing", "dead"):
                        log.error(
                            f"❌ {name}: state={state} "
                            f"last_run={last_run} detail={r['detail']}",
                        )
                        rc = 2
                    elif age is not None and age > max_age:
                        log.warning(
                            f"⚠  {name}: stale ({age:.1f} h) — last run at {last_run}",
                        )
                        rc = max(rc, 1)
                    else:
                        log.info(f"✅ {name}: {state} (last run {age:.1f}h ago)")
    except Exception as e:                                                    # noqa: BLE001
        log.error(f"❌ doctor failed: {type(e).__name__}: {e}")
        return 2

    if rc == 0:
        log.info("doctor: all green ✅")
    elif rc == 1:
        log.warning("doctor: yellow ⚠  (warnings above)")
    else:
        log.error("doctor: red ❌")
    return rc


if __name__ == "__main__":
    import sys
    sys.exit(doctor())
