"""Auto-scheduler — runs scan+classify+aggregate+ML forecast every N minutes.

Launched automatically by:
  1. The dashboard (Home.py) — when the UI opens
  2. A systemd user service (buffy-scheduler.service) — for boot-persistence
  3. `make schedule` / `python -m trend_hunter.flows.daily` — manual CLI

The scheduler catches every error per tick so a single failed scraper never
brings down the whole pipeline. This file is meant to run as a long-lived
background daemon — it blocks forever in the foreground.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from trend_hunter.core.config import get_settings
from trend_hunter.core.logging import configure


def _safe_tick() -> None:
    """Run one full pipeline cycle with per-phase storage managed internally.

    Each phase opens/closes its own writer so the DB lock is only held
    during actual writes (seconds), not during network scraping (minutes).
    """
    from trend_hunter.adapters.storage_duckdb import open_storage
    from trend_hunter.ingest.runner import run_once
    from trend_hunter.intelligence.aggregator import aggregate, classify_all
    from trend_hunter.intelligence.ml_forecaster import run_ml_forecast

    try:
        # Phase 1: Scrape — run_once() manages its own storage internally,
        # only holding the writer lock during the brief persist step at the end.
        logger.info("[scheduler] tick: starting scan...")
        summary = asyncio.run(run_once())  # no storage passed = lock-free scraping
        logger.info(f"[scheduler] tick: scan done — {summary}")

        # Phase 2: Classify + aggregate — open writer briefly, then close.
        try:
            with open_storage() as storage:
                agg = aggregate(storage)
                cls = classify_all(storage)
                logger.info(f"[scheduler] tick: aggregate={agg}, classified={cls}")
        except Exception as exc:
            logger.warning(f"[scheduler] tick: classify/aggregate failed: {exc}")

        # Phase 3: ML forecast — open writer briefly, then close.
        try:
            with open_storage() as storage:
                ml_result = run_ml_forecast(storage)
                logger.info(f"[scheduler] tick: ml_forecast ⇒ {ml_result}")
        except Exception as exc:
            logger.warning(f"[scheduler] tick: ml_forecast failed: {exc}")

        logger.info("[scheduler] tick: completed ✅")
    except Exception as exc:
        logger.error(f"[scheduler] tick: pipeline crashed: {exc}")


def start(skip_first: bool = False) -> BackgroundScheduler:
    """Build and start the in-process APScheduler.

    Parameters
    ----------
    skip_first:
        If True, skip the initial immediate tick and wait for the first
        interval to elapse.  Useful when the service restarts and a recent
        tick already ran.
    """
    s = get_settings()
    configure(s.log_dir, s.log_level)

    sched = BackgroundScheduler(daemon=True)

    if not skip_first:
        sched.add_job(
            _safe_tick,
            trigger="date",
            run_date=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5),
            id="initial_tick",
            max_instances=1,
        )

    sched.add_job(
        _safe_tick,
        trigger="interval",
        minutes=s.scan_interval_minutes,
        id="scan_tick",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    sched.start()
    logger.info(
        f"[scheduler] started: scan every {s.scan_interval_minutes}m, "
        f"skip_first={skip_first}",
    )
    return sched


if __name__ == "__main__":
    import signal
    import sys

    sched = start()

    def _handle_signal(signum: int, _frame) -> None:  # type: ignore[no-untyped-def]
        logger.info(f"[scheduler] received signal {signum}, shutting down...")
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)
