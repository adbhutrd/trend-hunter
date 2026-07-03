"""Run ledger — record every CLI invocation into the ``run_history`` table.

Pattern::

    with record_run("scan") as rec:
        n = run_once(storage)
        rec.set_counters({"products_in": n})

* On normal exit ``status='ok'`` and the row's ``finished_at`` is set.
* On exception ``status='err'`` and ``error_type``/``error_message`` are filled
  in; the exception is re-raised.
* ``dedupe_key`` lets the caller collapse retried runs into a single row.
* Fully idempotent: relaunching the same dedupe_key overwrites the prior row.

All I/O goes through :class:`DuckDBStorage` so the row writes participate in
the same FCNTL-lock + schema-version contract as every other writer.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from trend_hunter.adapters.storage_duckdb import DuckDBStorage


# ── helpers ──────────────────────────────────────────────────────────────────
def _make_run_id() -> str:
    """UUIDv7-style (time-prefixed) so inserts land roughly in start order."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    base = uuid.uuid4().bytes
    # Stamp the high 6 bytes with millis-since-epoch for SQL ORDER BY friendliness.
    tail = base[6:]
    stamped = ms.to_bytes(6, "big") + tail
    return str(uuid.UUID(bytes=stamped))


class _Recorder:
    """Mutable bag the caller mutates inside the ``with`` block."""

    __slots__ = ("_counters",)

    def __init__(self) -> None:
        self._counters: MutableMapping[str, int | float | str | bool] = {}

    def set_counters(self, counters: MutableMapping[str, Any]) -> None:
        """Replace / merge the counters dict. Values must be JSON-friendly scalars."""
        self._counters.update(counters)

    def incr(self, key: str, by: int | float = 1) -> None:
        self._counters[key] = self._counters.get(key, 0) + by

    @property
    def counters(self) -> dict[str, Any]:
        return dict(self._counters)


def _safe_json(payload: dict) -> str:
    return json.dumps(payload or {}, default=str)


# ── public entry-point ────────────────────────────────────────────────────────
@contextmanager
def record_run(
    command: str,
    *,
    storage: DuckDBStorage | None = None,
    db_path: Path | None = None,
    dedupe_key: str | None = None,
) -> Iterator[_Recorder]:
    """Context manager — records the run, yields a recorder, commits on exit.

    Parameters
    ----------
    command:
        The CLI command name, e.g. ``"scan"``, ``"aggregate"``,
        ``"money-sweep"``, ``"forget"``.
    storage:
        Optional *already-open* writer ``DuckDBStorage`` to reuse. Useful
        in tests (where the fixture holds the only writer lock) and in
        CLI flows that already opened a writer for the same DB. If passed,
        the ledger does NOT close the storage on exit (the caller owns it).
    db_path:
        DuckDB path (defaults to ``settings.db_path``). Creates a new
        writer for the duration of the context.
    dedupe_key:
        Optional stable identifier so retries collapse into one row. Two
        runs with the same ``(command, dedupe_key)`` overwrite each other.
    """
    from trend_hunter.core.config import get_settings

    rec = _Recorder()
    run_id = _make_run_id()
    host = socket.gethostname()
    pid = os.getpid()
    host_label = f"{host}:{pid}"
    started_at_ms = int(time.time() * 1000)

    if storage is not None and db_path is not None:
        raise ValueError("pass `storage` or `db_path`, not both")

    own_storage = False
    if storage is None:
        target = Path(db_path or get_settings().db_path)
        storage = DuckDBStorage(target, read_only=False)
        own_storage = True
    try:
        # Idempotency: if (command, dedupe_key) is already present, REUSE the
        # existing run_id so retries don't multiply rows.  Otherwise insert.
        if dedupe_key:
            existing = storage.query(
                "SELECT run_id FROM run_history WHERE command = ? AND dedupe_key = ?",
                (command, dedupe_key),
            )
            if existing:
                run_id = str(existing[0]["run_id"])
                storage.execute(
                    "UPDATE run_history SET started_at = current_timestamp, "
                    "status = 'pending', counters = NULL::JSON, "
                    "error_type = NULL, error_message = NULL "
                    "WHERE run_id = ?",
                    (run_id,),
                )
            else:
                storage.execute(
                    "INSERT INTO run_history "
                    "(run_id, command, started_at, status, host, dedupe_key) "
                    "VALUES (?, ?, current_timestamp, 'pending', ?, ?)",
                    (run_id, command, host_label, dedupe_key),
                )
        else:
            storage.execute(
                "INSERT INTO run_history "
                "(run_id, command, started_at, status, host, dedupe_key) "
                "VALUES (?, ?, current_timestamp, 'pending', ?, NULL)",
                (run_id, command, host_label),
            )

        # ── yield to the caller ────────────────────────────────────────────
        yield rec

        finished_ms = int(time.time() * 1000)
        storage.execute(
            "UPDATE run_history SET "
            "  finished_at = current_timestamp, "
            "  duration_ms = ?, "
            "  status = 'ok', "
            "  counters = ?::JSON "
            "WHERE run_id = ?",
            (finished_ms - started_at_ms, _safe_json(rec.counters), run_id),
        )
    except BaseException as exc:  # noqa: BLE001
        try:
            finished_ms = int(time.time() * 1000)
            storage.execute(
                "UPDATE run_history SET "
                "  finished_at = current_timestamp, "
                "  duration_ms = ?, "
                "  status = 'err', "
                "  counters = ?::JSON, "
                "  error_type = ?, "
                "  error_message = ? "
                "WHERE run_id = ?",
                (
                    finished_ms - started_at_ms,
                    _safe_json(rec.counters),
                    type(exc).__name__,
                    str(exc)[:1024],
                    run_id,
                ),
            )
        finally:
            if own_storage:
                storage.close()
        if isinstance(exc, (GeneratorExit, KeyboardInterrupt)):
            return
        raise
    else:
        if own_storage:
            storage.close()


# ── read-side helpers (used by loop-report and the dashboard) ─────────────────
def recent_runs(storage: DuckDBStorage, days: int = 7) -> list[dict]:
    return storage.query(
        f"""
        SELECT command, status, started_at, finished_at, duration_ms,
               counters, error_type, error_message
        FROM run_history
        WHERE started_at >= now() - INTERVAL {int(days)} DAY
        ORDER BY started_at DESC
        """,
        (),
    )


def success_rate(storage: DuckDBStorage, command: str, days: int = 7) -> float:
    rows = storage.query(
        f"""
        SELECT status, count(*) AS n
        FROM run_history
        WHERE command = ?
          AND started_at >= now() - INTERVAL {int(days)} DAY
        GROUP BY status
        """,
        (command,),
    )
    if not rows:
        return 0.0
    total = sum(int(r["n"]) for r in rows)
    ok = sum(int(r["n"]) for r in rows if r["status"] == "ok")
    return (ok / total) if total else 0.0


def last_run(storage: DuckDBStorage, command: str) -> dict | None:
    rows = storage.query(
        "SELECT * FROM run_history WHERE command = ? ORDER BY started_at DESC LIMIT 1",
        (command,),
    )
    return rows[0] if rows else None
