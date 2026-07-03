"""Daily in-process scheduler — every N minutes run scan+classify+aggregate,
and at 03:00 local time run the nightly Parquet aggregate.

We use APScheduler (already a project dep) without starting the daemon
automatically — operators call `python -m trend_hunter.flows.daily` themselves
inside a systemd user service on a true always-on host. For laptop-local
development, `make run` does the same work in a synchronous one-shot.
"""

from __future__ import annotations

import datetime as dt

from apscheduler.schedulers.background import BackgroundScheduler

from trend_hunter.core.config import get_settings
from trend_hunter.core.logging import configure, get


def _tick_scan() -> None:
    from trend_hunter.adapters.storage_duckdb import open_storage
    from trend_hunter.ingest.runner import run_once
    from trend_hunter.intelligence.aggregator import aggregate, classify_all

    log = get()
    with open_storage() as storage:
        summary = run_once(storage)
        aggregate(storage)
        classify_all(storage)
    log.info(f"[scheduler] scan tick ⇒ {summary}")


def start() -> BackgroundScheduler:
    """Build and start the in-process scheduler. Returns the live scheduler."""
    s = get_settings()
    configure(s.log_dir, s.log_level)
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(
        _tick_scan,
        trigger="interval",
        minutes=s.scan_interval_minutes,
        id="scan_tick",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        _tick_scan,
        trigger="cron",
        hour=s.aggregate_hour,
        minute=5,
        id="aggregate_tick",
        max_instances=1,
    )
    sched.start()
    get().info(
        f"[scheduler] started: scan every {s.scan_interval_minutes}m, "
        f"aggregate at {s.aggregate_hour:02d}:05 local",
    )
    return sched


if __name__ == "__main__":
    sched = start()
    try:
        # Block forever; Ctrl-C cleanly stops the scheduler.
        while True:
            dt.datetime.now(dt.UTC).timestamp()
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)
