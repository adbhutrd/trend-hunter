"""``loop-report`` — surface the systems state through one ``run_history`` table.

Prints:

* last run per command (``started_at``, ``status``, ``duration_ms``, ``error``)
* 7-day throughput per command + success rate
* 7-day totals for ingest-shaped counters (``products_in``,
  ``classified``, ``money_rows``, ``scaffolded``, ``audits``)
* dollars-on-track: 7-day mean ``retail_price * margin_pct`` from ``money``
* "delta vs prior 7d" so drift is visible at a glance
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from trend_hunter.adapters.storage_duckdb import DuckDBStorage
from trend_hunter.observe.run_ledger import recent_runs

# Commands the report treats as "core" — others go in the bottom panel.
CORE_COMMANDS = (
    "scan", "run", "aggregate", "arbitrage", "scaffold",
    "money-sweep", "forget", "validate-ads",
)

# Counters the report reads from the ``counters`` JSON column.
COUNTER_KEYS = (
    "products_in", "classified", "leads_added",
    "money_rows", "scaffolded", "audits", "validated",
)


def _format_age(ts) -> str:                                                  # noqa: ANN001
    if ts is None:
        return "—"
    delta = datetime.now(UTC) - ts
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86_400:
        return f"{s // 3600}h ago"
    return f"{s // 86_400}d ago"


def _counters_sum(rows: list[dict], key: str) -> int | float:
    total = 0
    for r in rows:
        c = r.get("counters")
        if not c:
            continue
        try:
            payload = c if isinstance(c, dict) else json.loads(c)
        except (json.JSONDecodeError, TypeError):
            continue
        if key in payload:
            try:
                total += float(payload[key])
            except (ValueError, TypeError):
                continue
    return total


def _success_rate(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    total = len(rows)
    ok = sum(1 for r in rows if r["status"] == "ok")
    return (ok / total) if total else 0.0


def render(storage: DuckDBStorage, days: int = 7) -> str:
    rows_7d = recent_runs(storage, days=days)
    rows_prev = recent_runs(storage, days=days * 2) if days > 0 else []
    cutoff = datetime.now(UTC) - timedelta(days=days)
    prev_window = [r for r in rows_prev
                   if r.get("started_at") and r["started_at"] < cutoff]

    out: list[str] = []
    out.append(
        f"loop report — last {days} days, {len(rows_7d)} run(s) captured "
        f"at {datetime.now(UTC):%Y-%m-%d %H:%M UTC}\n",
    )

    # ── 1) Last run per command ──────────────────────────────────────────────
    out.append("▸ last run per command")
    last_by_cmd: dict[str, dict] = {}
    for r in rows_7d:
        if r["command"] not in last_by_cmd:
            last_by_cmd[r["command"]] = r
    if not last_by_cmd:
        out.append(
            "    (no rows yet — run a few `make` targets "
            "to populate)\n",
        )
    else:
        for cmd, r in sorted(last_by_cmd.items()):
            ok = ("✅" if r["status"] == "ok"
                  else "❌" if r["status"] == "err"
                  else "⏳")
            age = _format_age(r.get("started_at"))
            dur = f"{r.get('duration_ms', 0)}ms" if r.get("duration_ms") else "—"
            err = f" ({r['error_type']})" if r.get("error_type") else ""
            out.append(f"    {ok} {cmd:14} {age:>10}  {dur:>9}{err}")
        out.append("")

    # ── 2) Throughput per command + success rate ─────────────────────────────
    out.append(f"▸ throughput & success — last {days} days (vs prior {days}d)")
    any_rows = False
    for cmd in CORE_COMMANDS:
        cur = [r for r in rows_7d if r["command"] == cmd]
        prv = [r for r in prev_window if r["command"] == cmd]
        if not cur and not prv:
            continue
        any_rows = True
        cur_n, prv_n = len(cur), len(prv)
        delta = cur_n - prv_n
        sign = "↑" if delta > 0 else "↓" if delta < 0 else "→"
        rc = _success_rate(cur)
        rc_p = _success_rate(prv)
        out.append(
            f"    {cmd:14} runs {cur_n:>4} {sign} {prv_n:<4} "
            f"succ {rc*100:5.1f}% (was {rc_p*100:5.1f}%)",
        )
    if not any_rows:
        out.append("    (no runs recorded for any core command yet)")
    out.append("")

    # ── 3) Counter totals ────────────────────────────────────────────────────
    out.append(f"▸ key counters — last {days} days")
    any_counters = False
    for k in COUNTER_KEYS:
        v = _counters_sum(rows_7d, k)
        if v:
            any_counters = True
            out.append(f"    {k:14} = {int(v)}")
    if not any_counters:
        out.append("    (no counters written yet — `make run` to populate)")
    out.append("")

    # ── 4) Dollars on track ──────────────────────────────────────────────────
    try:
        margin_rows = storage.query(
            f"""
            SELECT SUM(retail_price * margin_pct) AS track_dollars,
                   AVG(margin_pct) AS avg_margin
            FROM money
            WHERE recorded_at >= now() - INTERVAL {int(days)} DAY
            """,
            (),
        )
        if margin_rows and margin_rows[0]["track_dollars"] is not None:
            out.append(f"▸ dollars on track (money table, last {days} days)")
            out.append(
                f"    Σ(retail × margin)  = "
                f"${margin_rows[0]['track_dollars']:,.2f}",
            )
            out.append(
                f"    avg margin          = "
                f"{margin_rows[0]['avg_margin'] * 100:.1f}%",
            )
    except Exception as e:                                                    # noqa: BLE001
        out.append(f"▸ dollars on track: skipped ({type(e).__name__})")

    out.append("")
    out.append(
        "hint: try `make loop-report` after a few `make run`s. "
        "Sample ledger: `cli loop-report --days 30`.",
    )
    return "\n".join(out)


def day_buckets(rows: list[dict]) -> dict[str, int]:
    """Counts of runs per ISO date — handy for the dashboard sparkline."""
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        ts = r.get("started_at")
        if ts is None:
            continue
        try:
            out[ts.strftime("%Y-%m-%d")] += 1  # type: ignore[union-attr]
        except AttributeError:
            continue
    return dict(out)
