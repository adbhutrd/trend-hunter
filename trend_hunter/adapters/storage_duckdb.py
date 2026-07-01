"""DuckDB storage adapter — the production implementation of core.ports.Storage.

Design points (religiously):
* Writer holds an FCNTL lock — multi-process safety on a single laptop
* Reader mode (Streamlit) opens a fresh, cheap, read-only connection per call
* Schema migrations are idempotent, versioned, applied once on writer connect
* Upserts via pandas DF → DuckDB's first-class pandas bridge (fast)
* Composite primary keys + `ON CONFLICT DO NOTHING` → idempotent for re-runs
* `query()` returns list[dict] so the protocol stays mapping-friendly
* Aggregation tables live in same DB so dashboard reads are zero-DSP
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import os
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from trend_hunter.core.types import Health, HealthState

# ── schema (one migration = one ordered number) ────────────────────────────
# NOTE on column names: DuckDB reserves some tokens for time-travel queries
# (e.g. `AT (VERSION => ...)`), so we use `recorded_at` instead of `at`
# for the audit log. Don't rename to `at` without a quick smoke test.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, """
        CREATE TABLE IF NOT EXISTS products (
            source        TEXT NOT NULL,
            external_id   TEXT NOT NULL,
            captured_at   TIMESTAMP NOT NULL,
            title         TEXT,
            price         DOUBLE,
            currency      TEXT,
            url           TEXT,
            payload       JSON,
            PRIMARY KEY (source, external_id, captured_at)
        );
        CREATE INDEX IF NOT EXISTS idx_products_extid
            ON products(source, external_id);
        CREATE INDEX IF NOT EXISTS idx_products_captured
            ON products(captured_at);

        CREATE TABLE IF NOT EXISTS leads (
            domain        TEXT NOT NULL,
            company_name  TEXT,
            contact_email TEXT,
            contact_url   TEXT,
            source_url    TEXT,
            score         DOUBLE,
            captured_at   TIMESTAMP NOT NULL,
            PRIMARY KEY (domain, captured_at)
        );
        CREATE INDEX IF NOT EXISTS idx_leads_captured
            ON leads(captured_at);

        CREATE TABLE IF NOT EXISTS money (
            sku           TEXT PRIMARY KEY,
            retail_price  DOUBLE,
            supplier_cost DOUBLE,
            shipping_cost DOUBLE,
            cac_estimate  DOUBLE,
            margin_pct    DOUBLE,
            currency      TEXT,
            recorded_at   TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS health (
            source       TEXT PRIMARY KEY,
            state        TEXT,
            last_run     TIMESTAMP,
            last_ok      TIMESTAMP,
            rows_in      BIGINT DEFAULT 0,
            error_rate   DOUBLE,
            detail       TEXT
        );

        -- Art. 30 GDPR Record-of-Processing audit.
        -- subject_hash is the SHA-256 prefix of the contact email;
        -- the email itself is NEVER stored here (forgotten at the door).
        -- Composite (subject_hash, recorded_at) PK → idempotent across repeated forget calls.
        CREATE TABLE IF NOT EXISTS rop_audit (
            subject_hash  TEXT NOT NULL,
            op_type       TEXT,
            reason        TEXT,
            recorded_at   TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (subject_hash, recorded_at)
        );
        CREATE INDEX IF NOT EXISTS idx_rop_recorded ON rop_audit(recorded_at);

        CREATE TABLE IF NOT EXISTS forecasts (
            sku            TEXT NOT NULL,
            horizon_days   INTEGER NOT NULL,
            point_estimate DOUBLE,
            lower_80       DOUBLE,
            upper_80       DOUBLE,
            confidence     DOUBLE,
            captured_at    TIMESTAMP NOT NULL,
            PRIMARY KEY (sku, captured_at)
        );
    """),
]


class DuckDBStorage:
    """Production Storage adapter for DuckDB.

    Parameters
    ----------
    db_path:
        Where the single DuckDB file lives. Will be created if missing.
    read_only:
        * True  — open fresh read-only conn per query. Used by Streamlit.
        * False — hold a single writer conn + flock. Used by CLI/scrapers.
    """

    def __init__(self, db_path: Path, *, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.read_only = read_only
        self._writer_conn: duckdb.DuckDBPyConnection | None = None
        self._lock_fh: Any | None = None
        self._col_cache: dict[str, list[str]] = {}

        if not read_only:
            self._acquire_writer_lock()

    # ── writer lock (one process at a time) ─────────────────────────────────
    def _acquire_writer_lock(self) -> None:
        lock_path = self.db_path.parent / f".{self.db_path.name}.writer.lock"
        # O_CREAT|O_RDWR with explicit mode 0o600 — avoids `open(.., "w")`
        # racing with concurrent creators that truncate differently.
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        self._lock_fh = os.fdopen(fd, "r+b")
        try:
            fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                self._lock_fh.close()
                self._lock_fh = None
                raise RuntimeError(
                    f"Another writer holds the lock on {self.db_path}. "
                    "Close any other running CLI before starting a new scan.",
                ) from e
            self._lock_fh.close()
            self._lock_fh = None
            raise

    # ── connection ──────────────────────────────────────────────────────────
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self.read_only:
            return duckdb.connect(str(self.db_path), read_only=True)
        if self._writer_conn is None:
            self._writer_conn = duckdb.connect(str(self.db_path))
            self._writer_conn.execute("PRAGMA threads=4")
            self._migrate(self._writer_conn)
        return self._writer_conn

    def _migrate(self, c: duckdb.DuckDBPyConnection) -> None:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS _schema_version (
                version     INTEGER PRIMARY KEY,
                applied_at  TIMESTAMP DEFAULT current_timestamp
            )
            """,
        )
        for v, sql in _MIGRATIONS:
            already = c.execute(
                "SELECT count(*) FROM _schema_version WHERE version = ?",
                [v],
            ).fetchone()
            if already and already[0] == 0:
                # Wrap each migration in a transaction so a partial failure
                # doesn't leave the schema wedged.
                c.execute("BEGIN")
                try:
                    c.execute(sql)
                    c.execute(
                        "INSERT INTO _schema_version(version) VALUES (?)",
                        [v],
                    )
                    c.execute("COMMIT")
                except Exception:
                    c.execute("ROLLBACK")
                    raise

    # ── cached column introspection to avoid extra round-trips ───────────────
    def _columns(self, table: str) -> list[str]:
        if table not in self._col_cache:
            rows = self.conn().execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position",
                [table],
            ).fetchall()
            self._col_cache[table] = [r[0] for r in rows]
        return self._col_cache[table]

    # ── Storage protocol ───────────────────────────────────────────────────
    def upsert(self, table: str, rows: list[dict]) -> int:
        if self.read_only:
            raise RuntimeError("read-only storage cannot upsert")
        if not rows:
            return 0
        c = self.conn()
        df = pd.DataFrame(rows)
        # Reindex to schema columns: keeps us safe if a caller hands in a dict
        # with an extra key (e.g. a future `payload_debug` leak). Unknown
        # columns are dropped; missing columns become NaN → NULL in DuckDB.
        df = df.reindex(columns=self._columns(table))
        # Composite PKs + ON CONFLICT DO NOTHING ⇒ idempotent for re-runs.
        c.execute(
            f"INSERT INTO {table} SELECT * FROM df ON CONFLICT DO NOTHING",
        )
        return len(rows)

    def query(
        self,
        sql: str,
        params: tuple = (),
    ) -> list[dict]:
        c = self.conn()
        rel = c.execute(sql, list(params) if params else [])
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=False)) for row in rel.fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> None:
        c = self.conn()
        c.execute(sql, list(params) if params else [])

    async def health(self) -> list[Health]:
        c = self.conn()
        rows = c.execute(
            """
            SELECT source, state, last_run, last_ok, rows_in, error_rate, detail
            FROM health ORDER BY source
            """,
        ).fetchall()
        out: list[Health] = []
        for r in rows:
            out.append(
                Health(
                    source=r[0],
                    state=HealthState(r[1] or "unknown"),
                    last_run=r[2],
                    last_ok=r[3],
                    rows_in=int(r[4] or 0),
                    error_rate=float(r[5] or 0.0),
                    detail=r[6] or "",
                ),
            )
        return out

    # ── cleanup ─────────────────────────────────────────────────────────────
    def close(self) -> None:
        if self._writer_conn is not None:
            with contextlib.suppress(Exception):
                self._writer_conn.close()
            self._writer_conn = None
        if self._lock_fh is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
                self._lock_fh.close()
            self._lock_fh = None

    def __enter__(self) -> DuckDBStorage:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# ── convenience factory ─────────────────────────────────────────────────────
def open_storage(read_only: bool = False) -> DuckDBStorage:
    """Lazy import to avoid pulling config into the type-definition module."""
    from trend_hunter.core.config import get_settings

    return DuckDBStorage(get_settings().db_path, read_only=read_only)
