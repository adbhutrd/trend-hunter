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
    python -m trend_hunter.cli schedule                    # start APScheduler
    python -m trend_hunter.cli dashboard
    python -m trend_hunter.cli stats
    python -m trend_hunter.cli doctor
    python -m trend_hunter.cli forget <email>
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from trend_hunter.core.config import get_settings
from trend_hunter.core.logging import configure, get


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
    with _open_writer() as storage:
        summary = asyncio.run(run_once(storage))
    get().info(f"scan summary: {summary}")


def _cmd_run(_args: argparse.Namespace) -> None:
    """scan + classify + aggregate + doctor — single writer session + read-only doctor."""
    log = get()
    from trend_hunter.intelligence.aggregator import aggregate, classify_all
    from trend_hunter.ingest.runner import run_once
    from trend_hunter.scripts.doctor import doctor

    with _open_writer() as storage:
        scan_summary = asyncio.run(run_once(storage))
        log.info(f"scan summary: {scan_summary}")
        agg = aggregate(storage)
        n = classify_all(storage)
        log.info(f"aggregate: {agg}; classified={n}")
    rc = doctor()
    if rc != 0:
        sys.exit(rc)


def _cmd_aggregate(_args: argparse.Namespace) -> None:
    from trend_hunter.intelligence.aggregator import aggregate, classify_all
    with _open_writer() as storage:
        out = aggregate(storage)
        n = classify_all(storage)
    get().info(f"aggregate: {out}; classified={n}")


def _cmd_doctor(_args: argparse.Namespace) -> None:
    from trend_hunter.scripts.doctor import doctor
    sys.exit(doctor())


# ── Phase 3: forecast / arbitrage / ads / scaffold / money-sweep ──────────────
def _cmd_forecast(_args: argparse.Namespace) -> None:
    from datetime import datetime, timedelta, timezone
    from trend_hunter.adapters.forecaster_baseline import BaselineForecaster

    fc = BaselineForecaster()
    now = datetime.now(timezone.utc)
    history = [(now - timedelta(days=6 - i), 10.0 + 1.5 * i) for i in range(7)]
    out = fc.fit_predict(history, horizon_days=14)
    print(
        f"  BaselineForecaster({fc.name}): point={out.point_estimate:.2f} "
        f"CI[{out.lower_80:.2f}, {out.upper_80:.2f}] conf={out.confidence:.2f}"
    )


def _cmd_arbitrage(_args: argparse.Namespace) -> None:
    from pathlib import Path
    from trend_hunter.adapters.supplier_catalog import CsvCatalogSupplier, MockSupplier
    from trend_hunter.money.arbitrage import scan, summary

    with _open_writer() as storage:
        cat = CsvCatalogSupplier(Path("./data/suppliers.csv"))
        rows = scan(storage, cat if cat._items else MockSupplier())    # type: ignore[arg-type]
    s = summary(rows)
    print(f"  arbitrage: {s}")
    for r in rows[:10]:
        print(
            f"    {r.sku:40} retail=${r.retail_price:6.2f} "
            f"cost=${r.supplier_cost:5.2f} margin={r.margin_pct*100:5.1f}% "
            f"via {r.supplier_name}"
        )


def _cmd_validate_ads(_args: argparse.Namespace) -> None:
    from trend_hunter.adapters.ads_validator import AdsValidator
    v = AdsValidator()
    validated = v.validated()
    print(f"  ads: {len(v.all())} entries, {len(validated)} validated (≥30 days running)")
    for a in validated[:20]:
        print(
            f"    {a.advertiser:20} niche={a.niche:20} "
            f"{a.days_running:>4d}d  {a.source}"
        )


def _cmd_scaffold(args: argparse.Namespace) -> None:
    from pathlib import Path
    from trend_hunter.adapters.supplier_catalog import CsvCatalogSupplier, MockSupplier
    from trend_hunter.money.arbitrage import scan
    from trend_hunter.money.scaffold_pipeline import scaffold

    live = bool(args.live)
    top = int(args.top)
    with _open_writer() as storage:
        cat = CsvCatalogSupplier(Path("./data/suppliers.csv"))
        rows = scan(storage, cat if cat._items else MockSupplier())    # type: ignore[arg-type]
    out = scaffold(rows, top=top, dry_run=not live)
    print(f"  scaffold: top={top} dry_run={not live}; results:")
    for r in out:
        print(f"    {r.get('status'):12} sku={r.get('sku')}")
    if not live:
        print("  (set TH_SHOPIFY_DOMAIN + TH_SHOPIFY_TOKEN env vars and pass --live to actually push)")


def _cmd_money_sweep(args: argparse.Namespace) -> None:
    from pathlib import Path
    from trend_hunter.adapters.supplier_catalog import CsvCatalogSupplier, MockSupplier
    from trend_hunter.money.arbitrage import scan, summary
    from trend_hunter.money.scaffold_pipeline import scaffold

    live = bool(args.live)
    top = int(args.top)
    with _open_writer() as storage:
        cat = CsvCatalogSupplier(Path("./data/suppliers.csv"))
        supplier = cat if cat._items else MockSupplier()              # type: ignore[assignment]
        rows = scan(storage, supplier)
        s = summary(rows)
        print(f"  money sweep: {s}")
        out = scaffold(rows, top=top, dry_run=not live)
    print(f"  scaffold: {len(out)} push result(s):")
    for r in out:
        print(f"    {r.get('status'):12} sku={r.get('sku')}")


def _cmd_schedule(_args: argparse.Namespace) -> None:
    from trend_hunter.flows.daily import start
    sched = start()
    try:
        # block forever; systemd will stop us cleanly
        while True:
            import time
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)


# ── ops / UI ──────────────────────────────────────────────────────────────────
def _cmd_dashboard(_args: argparse.Namespace) -> None:
    import subprocess
    port = get_settings().dash_port
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        "trend_hunter/ui/Home.py",
        "--server.port", str(port),
        "--server.headless", "true",
    ]
    get().info(f"launching dashboard on http://localhost:{port}")
    subprocess.run(cmd, check=False)


def _cmd_stats(_args: argparse.Namespace) -> None:
    with _open_reader() as storage:
        for table in ("products", "leads", "money", "rop_audit", "health", "forecasts"):
            try:
                n = storage.query(f"SELECT count(*) AS n FROM {table}")[0]["n"]
            except Exception:
                n = "?"
            print(f"  {table:14}  {n}")


def _cmd_forget(args: argparse.Namespace) -> None:
    """GDPR right-to-erasure — drop lead rows + write hashed retention marker."""
    import hashlib
    subject_email = args.email
    subject_hash = hashlib.sha256(subject_email.encode()).hexdigest()[:16]
    with _open_writer() as storage:
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
        get().info(f"forget: {before} lead row(s) dropped; audit hash {subject_hash}")


# ── parser ────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trend-hunter",
        description="Self-hosted Shopify trend-finder, B2B lead-generator, and arbitrage engine.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="ingest from all enabled sources (once)").set_defaults(func=_cmd_scan)
    sub.add_parser("forecast", help="quick smoke-test of BaselineForecaster").set_defaults(func=_cmd_forecast)
    rp = sub.add_parser("run", help="scan + classify + aggregate + doctor")
    rp.set_defaults(func=_cmd_run)
    sub.add_parser("aggregate", help="build dashboard roll-ups").set_defaults(func=_cmd_aggregate)
    sub.add_parser("doctor", help="self-test (DB, schema, free disk, freshness)").set_defaults(func=_cmd_doctor)
    sub.add_parser("dashboard", help="launch Streamlit on configured port").set_defaults(func=_cmd_dashboard)
    sub.add_parser("stats", help="row counts per table").set_defaults(func=_cmd_stats)
    sub.add_parser("schedule", help="start APScheduler (foreground)").set_defaults(func=_cmd_schedule)

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

    fp = sub.add_parser("forget", help="GDPR Art. 17 right-to-erasure")
    fp.add_argument("email")
    fp.set_defaults(func=_cmd_forget)

    return p


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    configure(settings.log_dir, settings.log_level)
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
