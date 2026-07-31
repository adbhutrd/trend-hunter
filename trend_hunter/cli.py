"""CLI entrypoint — wires every command in the project.

Usage
-----
    python -m trend_hunter.cli scan
    python -m trend_hunter.cli run                         # full daily cycle
    python -m trend_hunter.cli forecast                    # baseline forecaster self-test
    python -m trend_hunter.cli aggregate                   # build dashboard roll-ups
    python -m trend_hunter.cli arbitrage                   # match trending ↔ suppliers
    python -m trend_hunter.cli validate-ads                # list ads ≥ 30 days
    python -m trend_hunter.cli scaffold --top 3 --live     # draft + push (dry-run by default)
    python -m trend_hunter.cli money-sweep --top 3         # arbitrage + scaffold end-to-end
    python -m trend_hunter.cli calibrate                   # forecast back-testing + MAPE
    python -m trend_hunter.cli schedule                    # start APScheduler
    python -m trend_hunter.cli dashboard
    python -m trend_hunter.cli stats
    python -m trend_hunter.cli doctor
    python -m trend_hunter.cli forget <email>
    python -m trend_hunter.cli loop-report                 # 7d feedback-loop summary
    python -m trend_hunter.cli alert                       # push notifications if degradation
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from trend_hunter.core.config import get_settings
from trend_hunter.core.logging import configure, get
from trend_hunter.intelligence.patterns import detect_multi_timeframe_pattern, detect_pattern
from trend_hunter.observe.run_ledger import record_run


# ── helpers ───────────────────────────────────────────────────────────────────
def _open_writer():
    from trend_hunter.adapters.storage_duckdb import open_storage

    return open_storage(read_only=False)


def _open_reader():
    from trend_hunter.adapters.storage_duckdb import open_storage

    return open_storage(read_only=True)


# ── ingest / aggregate ────────────────────────────────────────────────────────
def _cmd_scan(_args: argparse.Namespace) -> None:
    from trend_hunter.ingest.runner import run_once

    with _open_writer() as storage, record_run("scan", storage=storage) as rec:
        summary = asyncio.run(run_once(storage))
        rec.set_counters({"products_in": int(summary.get("total", 0))})
    get().info(f"scan summary: {summary}")


def _cmd_run(_args: argparse.Namespace) -> None:
    """scan + classify + aggregate + ML forecast + doctor — single writer session + read-only doctor."""
    from trend_hunter.ingest.runner import run_once
    from trend_hunter.intelligence.aggregator import aggregate, classify_all
    from trend_hunter.intelligence.ml_forecaster import run_ml_forecast
    from trend_hunter.scripts.doctor import doctor

    log = get()
    with _open_writer() as storage, record_run("run", storage=storage) as rec:
        scan_summary = asyncio.run(run_once(storage))
        log.info(f"scan summary: {scan_summary}")
        agg = aggregate(storage)
        cls = classify_all(storage)
        log.info(f"aggregate: {agg}; classified={cls}")
        ml_result = run_ml_forecast(storage)
        log.info(f"ml_forecast: {ml_result}")
        rec.set_counters(
            {
                "products_in": int(scan_summary.get("total", 0)),
                "classified": int(cls["products"]),
                "snapshots": int(cls["snapshots"]),
                "calibrations_resolved": int(agg.get("calibrations_resolved", 0)),
                "ml_predictions": int(ml_result.get("predictions", 0)),
            }
        )
    rc = doctor()
    if rc != 0:
        sys.exit(rc)


def _cmd_aggregate(_args: argparse.Namespace) -> None:
    from trend_hunter.intelligence.aggregator import aggregate, classify_all

    with _open_writer() as storage, record_run("aggregate", storage=storage) as rec:
        out = aggregate(storage)
        cls = classify_all(storage)
        rec.set_counters(
            {
                "classified": int(cls["products"]),
                "snapshots": int(cls["snapshots"]),
                "calibrations_resolved": int(out.get("calibrations_resolved", 0)),
            }
        )
    get().info(f"aggregate: {out}; classified={cls}")


def _cmd_doctor(_args: argparse.Namespace) -> None:
    from trend_hunter.scripts.doctor import doctor

    sys.exit(doctor())


# ── Phase 3: forecast / arbitrage / ads / scaffold / money-sweep / calibrate ──
def _cmd_forecast(_args: argparse.Namespace) -> None:
    from datetime import datetime, timedelta

    from trend_hunter.adapters.forecaster_baseline import BaselineForecaster
    from trend_hunter.observe.calibrate import record_calibration

    fc = BaselineForecaster()
    now = datetime.now(__import__("datetime").UTC)
    history = [(now - timedelta(days=6 - i), 10.0 + 1.5 * i) for i in range(7)]
    out = fc.fit_predict(history, horizon_days=14)
    print(
        f"  BaselineForecaster({fc.name}): point={out.point_estimate:.2f} "
        f"CI[{out.lower_80:.2f}, {out.upper_80:.2f}] conf={out.confidence:.2f}"
    )

    # Record calibration for this forecast (actual will be filled in later).
    with _open_writer() as storage:
        cid = record_calibration(
            storage,
            sku="forecast-smoke",
            predicted_price=out.point_estimate,
            horizon_days=14,
            source_command="forecast",
        )
        print(f"  calibration_id={cid} (update with `cli calibrate update <id> <actual_price>`)")


def _cmd_arbitrage(_args: argparse.Namespace) -> None:
    from trend_hunter.adapters.supplier_catalog import CsvCatalogSupplier, MockSupplier
    from trend_hunter.money.arbitrage import scan, summary

    with _open_writer() as storage, record_run("arbitrage", storage=storage) as rec:
        cat = CsvCatalogSupplier(Path("./data/suppliers.csv"))
        rows = scan(storage, cat if cat._items else MockSupplier())  # type: ignore[arg-type]
        rec.set_counters({"money_rows": summary(rows)["count"]})
    s = summary(rows)
    get().info(f"  arbitrage: {s}")
    for r in rows[:10]:
        print(
            f"    {r.sku:40} retail=${r.retail_price:6.2f} "
            f"cost=${r.supplier_cost:5.2f} margin={r.margin_pct * 100:5.1f}% "
            f"via {r.supplier_name}"
        )


def _cmd_validate_ads(_args: argparse.Namespace) -> None:
    from trend_hunter.adapters.ads_validator import AdsValidator

    with _open_writer() as storage, record_run("validate-ads", storage=storage) as rec:
        v = AdsValidator()
        validated = v.validated()
        rec.set_counters({"validated": len(validated)})
    print(
        f"  ads: {len(v.all())} entries, {len(validated)} validated (≥30 days running)",
    )
    for a in validated[:20]:
        print(f"    {a.advertiser:20} niche={a.niche:20} {a.days_running:>4d}d  {a.source}")


def _cmd_scaffold(args: argparse.Namespace) -> None:
    from trend_hunter.adapters.supplier_catalog import CsvCatalogSupplier, MockSupplier
    from trend_hunter.money.arbitrage import scan
    from trend_hunter.money.scaffold_pipeline import scaffold

    live = bool(args.live)
    top = int(args.top)
    with _open_writer() as storage, record_run("scaffold", storage=storage) as rec:
        cat = CsvCatalogSupplier(Path("./data/suppliers.csv"))
        rows = scan(storage, cat if cat._items else MockSupplier())  # type: ignore[arg-type]
        out = scaffold(rows, top=top, dry_run=not live)
        rec.set_counters(
            {
                "scaffolded": sum(1 for r in out if r.get("status") in {"drafted", "pushed"}),
            }
        )
    print(f"  scaffold: top={top} dry_run={not live}; results:")
    for r in out:
        print(f"    {r.get('status'):12} sku={r.get('sku')}")
    if not live:
        print(
            "  (set TH_SHOPIFY_DOMAIN + TH_SHOPIFY_TOKEN env vars "
            "and pass --live to actually push)",
        )


def _cmd_money_sweep(args: argparse.Namespace) -> None:
    from trend_hunter.adapters.supplier_catalog import CsvCatalogSupplier, MockSupplier
    from trend_hunter.money.arbitrage import scan, summary
    from trend_hunter.money.scaffold_pipeline import scaffold

    live = bool(args.live)
    top = int(args.top)
    with _open_writer() as storage, record_run("money-sweep", storage=storage) as rec:
        cat = CsvCatalogSupplier(Path("./data/suppliers.csv"))
        supplier = cat if cat._items else MockSupplier()  # type: ignore[assignment]
        rows = scan(storage, supplier)
        s = summary(rows)
        print(f"  money sweep: {s}")
        out = scaffold(rows, top=top, dry_run=not live)
        rec.set_counters(
            {
                "money_rows": s["count"],
                "scaffolded": sum(1 for r in out if r.get("status") in {"drafted", "pushed"}),
            }
        )
    print(f"  scaffold: {len(out)} push result(s):")
    for r in out:
        print(f"    {r.get('status'):12} sku={r.get('sku')}")

    # ── Run chaining: after money-sweep, auto-run loop-report + check alerts ──
    _chain_loop_report()
    _chain_alerts()


def _cmd_calibrate(args: argparse.Namespace) -> None:
    """Calibrate subcommand — forecast back-testing, MAPE reporting, etc.

    Sub-subcommands::

        calibrate list [--days 30]          # pending forecasts awaiting actuals
        calibrate update <id> <actual>      # fill in the realised price
        calibrate summary [--days 7]        # MAPE + MAE for resolved forecasts
    """
    # Ensure the calibrate table exists (open writer briefly).
    with _open_writer():
        pass

    sub = args.calibrate_cmd or "summary"
    with _open_reader() as storage:
        if sub == "list":
            from trend_hunter.observe.calibrate import pending_calibrations

            rows = pending_calibrations(storage, days=int(args.days))
            if rows:
                print(f"  pending calibrations ({len(rows)}):")
                for r in rows:
                    print(
                        f"    {r['calibrate_id'][:8]} sku={r['sku']:20} "
                        f"predicted=${r['predicted_price']:6.2f} "
                        f"horizon={r['horizon_days']}d "
                        f"via {r['source_command'] or '?'}"
                    )
            else:
                print("  (no pending calibrations — forecasts get actuals quickly)")
        elif sub == "update":
            from trend_hunter.observe.calibrate import update_actual

            if not args.calibrate_id or args.actual_price is None:
                print("usage: calibrate update <calibrate_id> <actual_price>")
                return
            with _open_writer() as storage_w:
                update_actual(storage_w, args.calibrate_id, float(args.actual_price))
            print(
                f"  calibration {args.calibrate_id[:8]} updated: actual=${float(args.actual_price):.2f}"
            )
        elif sub == "backfill":
            from trend_hunter.observe.calibrate import auto_resolve_calibrations

            dry = bool(getattr(args, "dry", False))
            with _open_writer() as storage_w:
                out = auto_resolve_calibrations(
                    storage_w,
                    days_lookback=int(args.days),
                    dry_run=dry,
                )
            print(f"  calibrate backfill (last {args.days}d, dry_run={dry}):")
            print(f"    total pending = {out['total']}")
            print(f"    resolved      = {out['resolved']}")
            print(f"    skipped       = {out['skipped']}")
        else:
            from trend_hunter.observe.calibrate import calibration_summary

            cal = calibration_summary(storage, days=int(args.days))
            if cal["count"]:
                print(f"  calibration summary (last {args.days}d):")
                print(f"    resolved forecasts = {cal['count']}")
                print(f"    MAPE               = {cal['mape_pct']:.2f}%")
                if cal["mean_abs_error"] is not None:
                    print(f"    mean abs error     = ${cal['mean_abs_error']:.2f}")
            else:
                print(f"  (no resolved calibrations in the last {args.days} days)")


def _cmd_alert(_args: argparse.Namespace) -> None:
    """Check every core command's 1-day success rate; alert on degradation."""
    from trend_hunter.observe.alerts import alert_on_degradation

    with _open_reader() as storage:
        offenders = alert_on_degradation(storage)
    if offenders:
        print("  alerts sent for degraded commands:")
        for o in offenders:
            print(f"    ⚠️  {o['command']:14} rate={o['rate'] * 100:.0f}%")
    else:
        print("  all core commands healthy (no alerts sent)")


def _cmd_pattern_alerts(_args: argparse.Namespace) -> None:
    """Check cross-timeframe pattern transitions and alert on breakout/accelerating."""
    from trend_hunter.observe.pattern_alerts import check_pattern_alerts

    with _open_writer() as storage:
        summary = check_pattern_alerts(storage)
    print(f"  checked {summary['checked']} product(s)")
    if summary["alerted"]:
        print(f"  🚀 alerted on {summary['alerted']} new breakout/accelerating product(s):")
        for p in summary["patterns"]:
            print(f"    • {p['pattern']} — {p['title']} ({p['source']}/{p['external_id']})")
    else:
        print("  no new breakout/accelerating transitions (no alerts sent)")


# ── store discovery ──────────────────────────────────────────────────────────
def _cmd_discover(args: argparse.Namespace) -> None:
    """Discover new Shopify stores and add them to sources.json."""
    from trend_hunter.adapters.store_hunter import StoreHunter

    async def _run():
        hunter = StoreHunter()
        if args.url:
            result = await hunter.discover_single(args.url)
            if result["valid"]:
                print(f"  ✅ {result['url']} — {result['reason']}")
                if result["added"]:
                    print("     Added to sources.json!")
                else:
                    print("     Already in sources.json.")
            else:
                print(f"  ❌ {result['url']} — {result['reason']}")
            return

        result = await hunter.discover()
        print("\n  📊 Store Discovery Results:")
        print(f"     Checked:  {result['total_checked']} stores")
        print(f"     Valid:    {result['valid']} stores")
        print(f"     New:      {result['new']} stores")
        print(f"     Removed:  {result['removed']} stores")
        if result.get("new_stores"):
            print("\n  🆕 New stores discovered:")
            for s in result["new_stores"]:
                print(f"     • {s}")

    import asyncio
    asyncio.run(_run())


# ── run chaining helpers ─────────────────────────────────────────────────────
def _chain_loop_report() -> None:
    """Auto-run loop-report after money-sweep."""
    try:
        from trend_hunter.observe.loop_report import render

        storage = _open_reader()
        try:
            get().info("run-chaining: loop-report")
            print(render(storage, days=7))
        finally:
            storage.close()
    except Exception as exc:  # noqa: BLE001
        get().warning("run-chaining: loop-report failed: %s", exc)


def _chain_alerts() -> None:
    """Auto-check success rates and push alerts if degradation detected."""
    try:
        from trend_hunter.observe.alerts import alert_on_degradation

        with _open_reader() as storage:
            offenders = alert_on_degradation(storage)
        if offenders:
            get().warning(
                "run-chaining: %d degraded command(s) alerted",
                len(offenders),
            )
    except Exception as exc:  # noqa: BLE001
        get().warning("run-chaining: alerts failed: %s", exc)


# ── ops / UI ──────────────────────────────────────────────────────────────────
def _cmd_schedule(_args: argparse.Namespace) -> None:
    from trend_hunter.flows.daily import start

    sched = start()
    try:
        while True:
            import time

            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)


def _cmd_dashboard(_args: argparse.Namespace) -> None:
    import subprocess

    port = get_settings().dash_port
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "trend_hunter/ui/Home.py",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
    ]
    get().info(f"launching dashboard on http://localhost:{port}")
    subprocess.run(cmd, check=False)


def _cmd_migrate(_args: argparse.Namespace) -> None:
    """Open the writer briefly so pending migrations get applied.

    Read-only CLI commands (stats, loop-report) do not auto-migrate;
    running ``python -m trend_hunter.cli migrate`` once after pulling
    new schema migrations keeps everything in sync.
    """
    log = get()
    with _open_writer() as storage:
        versions = storage.query(
            "SELECT version FROM _schema_version ORDER BY version",
        )
    log.info(
        f"migrate: applied schema versions = {[r['version'] for r in versions]}",
    )


def _cmd_stats(_args: argparse.Namespace) -> None:
    with _open_reader() as storage:
        for table in (
            "products",
            "leads",
            "money",
            "rop_audit",
            "health",
            "forecasts",
            "run_history",
            "calibrate",
            "corrective_actions",
        ):
            try:
                n = storage.query(f"SELECT count(*) AS n FROM {table}")[0]["n"]
            except Exception:
                n = "?"
            print(f"  {table:20}  {n}")


def _cmd_forget(args: argparse.Namespace) -> None:
    """GDPR right-to-erasure — drop lead rows + write hashed retention marker."""
    import hashlib

    subject_email = args.email
    subject_hash = hashlib.sha256(subject_email.encode()).hexdigest()[:16]
    with (
        _open_writer() as storage,
        record_run("forget", storage=storage, dedupe_key=subject_hash) as rec,
    ):
        before = storage.query(
            "SELECT count(*) AS n FROM leads WHERE contact_email = ?",
            (subject_email,),
        )[0]["n"]
        storage.execute(
            "DELETE FROM leads WHERE contact_email = ?",
            (subject_email,),
        )
        storage.execute(
            """
            INSERT INTO rop_audit
                (subject_hash, op_type, reason, recorded_at)
            VALUES (?, ?, ?, current_timestamp)
            ON CONFLICT DO NOTHING
            """,
            (
                subject_hash,
                "forget",
                f"GDPR Art. 17 erasure; matched rows={before}",
            ),
        )
        rec.set_counters({"audits": before})
        get().info(f"forget: {before} lead row(s) dropped; audit hash {subject_hash}")


# ── loop-report ────────────────────────────────────────────────────────────────
def _cmd_loop_report(args: argparse.Namespace) -> None:
    from trend_hunter.observe.loop_report import render

    days = int(args.days)
    with _open_reader() as storage:
        print(render(storage, days=days))

    # Run chaining: check alerts after loop-report
    _chain_alerts()


def _cmd_trends(args: argparse.Namespace) -> None:
    """Live historical trend report — detect patterns across multiple windows."""
    from trend_hunter.intelligence.patterns import (
        build_cross_timeframe_snapshots,
        group_snapshots_by_timeframe,
    )

    days = int(args.days)
    # Use a writer so record_run can log this invocation to run_history.
    with _open_writer() as storage, record_run("trends", storage=storage) as rec:
        rows = storage.query(
            f"""
            SELECT source, external_id, title, status, slope_pct_per_day,
                   n_points, window_start, window_end, timeframe_days
            FROM trend_history
            WHERE window_end >= now() - INTERVAL {int(days)} DAY
            ORDER BY source, external_id, timeframe_days, window_end ASC
            """,
        )

        # Per-timeframe historical patterns.
        by_tf = group_snapshots_by_timeframe(rows)
        patterns: list[dict] = []
        for (source, external_id, tf), hist in by_tf.items():
            pattern = detect_pattern(hist)
            if pattern:
                latest = hist[-1]
                patterns.append(
                    {
                        "source": source,
                        "external_id": external_id,
                        "title": latest.get("title") or external_id,
                        "timeframe_days": tf,
                        "pattern": pattern,
                        "status": latest.get("status", "unknown"),
                        "slope_pct_per_day": latest.get("slope_pct_per_day", 0.0),
                    }
                )

        # Cross-timeframe patterns.
        cross_groups = build_cross_timeframe_snapshots(rows)
        cross_patterns: list[dict] = []
        for snapshots in cross_groups:
            pattern = detect_multi_timeframe_pattern(snapshots)
            if pattern:
                latest = snapshots[-1]
                cross_patterns.append(
                    {
                        "source": latest["source"],
                        "external_id": latest["external_id"],
                        "title": latest.get("title") or latest["external_id"],
                        "timeframe_days": 0,
                        "pattern": pattern,
                        "status": latest.get("status", "unknown"),
                        "slope_pct_per_day": latest.get("slope_pct_per_day", 0.0),
                    }
                )

        rec.set_counters({"patterns_found": len(patterns) + len(cross_patterns)})

    unique_products = len({(r["source"], r["external_id"]) for r in rows})
    print(f"  Per-timeframe patterns (last {days} days):")
    print(
        f"  {'Pattern':<18} {'TF':>4} {'Status':<10} {'Slope/day':>10}  Product"
    )
    print("  " + "-" * 75)
    for p in sorted(patterns, key=lambda x: (x["pattern"], -x["timeframe_days"])):
        slope = float(p["slope_pct_per_day"] or 0.0) * 100
        print(
            f"  {p['pattern']:<18} {p['timeframe_days']:>3}d "
            f"{p['status']:<10} {slope:>+9.2f}%  {p['title'][:40]}"
        )

    print("\n  Cross-timeframe patterns:")
    print(f"  {'Pattern':<18} {'Status':<10} {'Slope/day':>10}  Product")
    print("  " + "-" * 70)
    for p in sorted(cross_patterns, key=lambda x: x["pattern"]):
        slope = float(p["slope_pct_per_day"] or 0.0) * 100
        print(
            f"  {p['pattern']:<18} {p['status']:<10} {slope:>+9.2f}%  {p['title'][:40]}"
        )

    print(
        f"\n  Analyzed {unique_products} product(s), "
        f"found {len(patterns)} per-timeframe and {len(cross_patterns)} cross-timeframe patterns."
    )


# ── parser ────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trend-hunter",
        description="Self-hosted Shopify trend-finder, B2B lead-generator, and arbitrage engine.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="ingest from all enabled sources (once)").set_defaults(
        func=_cmd_scan
    )
    sub.add_parser(
        "forecast", help="quick smoke-test of BaselineForecaster + calibration record"
    ).set_defaults(func=_cmd_forecast)
    rp = sub.add_parser("run", help="scan + classify + aggregate + doctor")
    rp.set_defaults(func=_cmd_run)
    sub.add_parser("aggregate", help="build dashboard roll-ups").set_defaults(func=_cmd_aggregate)
    sub.add_parser("doctor", help="self-test (DB, schema, free disk, freshness)").set_defaults(
        func=_cmd_doctor
    )
    sub.add_parser("dashboard", help="launch Streamlit on configured port").set_defaults(
        func=_cmd_dashboard
    )
    sub.add_parser("stats", help="row counts per table").set_defaults(func=_cmd_stats)
    sub.add_parser(
        "migrate",
        help="open writer briefly to apply pending schema migrations",
    ).set_defaults(func=_cmd_migrate)
    sub.add_parser("schedule", help="start APScheduler (foreground)").set_defaults(
        func=_cmd_schedule
    )

    ap = sub.add_parser("arbitrage", help="match trending products ↔ suppliers")
    ap.set_defaults(func=_cmd_arbitrage)

    adp = sub.add_parser("validate-ads", help="list ads ≥ 30 days running (validated profit)")
    adp.set_defaults(func=_cmd_validate_ads)

    scp = sub.add_parser("scaffold", help="draft + push top-N profitable rows (dry-run default)")
    scp.add_argument("--top", type=int, default=3, help="how many drafts to push (default 3)")
    scp.add_argument("--live", action="store_true", help="actually POST to Shopify Admin API")
    scp.set_defaults(func=_cmd_scaffold)

    msp = sub.add_parser("money-sweep", help="arbitrage + scaffold in one call")
    msp.add_argument("--top", type=int, default=3)
    msp.add_argument("--live", action="store_true")
    msp.set_defaults(func=_cmd_money_sweep)

    # ── calibrate ────────────────────────────────────────────────────────────
    calp = sub.add_parser("calibrate", help="forecast back-testing and MAPE reporting")
    calp.add_argument(
        "calibrate_cmd",
        nargs="?",
        choices=("list", "update", "summary", "backfill"),
        default="summary",
        help="list pending, update actual, show MAPE summary, or backfill overdue rows",
    )
    calp.add_argument(
        "--dry",
        action="store_true",
        help="with `backfill`: report counts without writing to calibrate",
    )
    calp.add_argument("--days", type=int, default=7, help="lookback window (default 7)")
    calp.add_argument("calibrate_id", nargs="?", help="calibrate_id for 'update' subcommand")
    calp.add_argument(
        "actual_price", nargs="?", type=float, help="actual price for 'update' subcommand"
    )
    calp.set_defaults(func=_cmd_calibrate)

    # ── alert ────────────────────────────────────────────────────────────────
    alrtp = sub.add_parser("alert", help="check success rates and push notifications")
    alrtp.set_defaults(func=_cmd_alert)

    # ── pattern-alerts (cross-timeframe breakout/accelerating transitions) ────
    patp = sub.add_parser(
        "pattern-alerts",
        help="check cross-timeframe pattern transitions and alert on breakout/accelerating",
    )
    patp.set_defaults(func=_cmd_pattern_alerts)

    fp = sub.add_parser("forget", help="GDPR Art. 17 right-to-erasure")
    fp.add_argument("email")
    fp.set_defaults(func=_cmd_forget)

    lrp = sub.add_parser(
        "loop-report",
        help="7d feedback-loop summary from run_history",
    )
    lrp.add_argument("--days", type=int, default=7)
    lrp.set_defaults(func=_cmd_loop_report)

    # ── discover (Shopify store discovery) ───────────────────────────────
    dscp = sub.add_parser(
        "discover",
        help="discover new Shopify stores and update sources.json",
    )
    dscp.add_argument(
        "--url", type=str, default=None,
        help="validate a single URL and add if Shopify",
    )
    dscp.set_defaults(func=_cmd_discover)

    # ── trends (historical pattern report) ─────────────────────────────────
    trp = sub.add_parser(
        "trends",
        help="live historical trend report with pattern detection",
    )
    trp.add_argument("--days", type=int, default=30, help="lookback window (default 30)")
    trp.set_defaults(func=_cmd_trends)

    return p


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    configure(settings.log_dir, settings.log_level)
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
