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
    (
        1,
        """
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
    """,
    ),
    (
        2,
        """
        -- Migration #2: run history (loop-engineering feedback spine).
        -- Every CLI invocation writes one row so we can compute
        -- success rates, throughput, payload drift over time.
        -- run_id is a deterministic UUIDv7-ish so concurrent runs
        -- don't collide and retried runs collapse via
        -- (command, dedupe_key) unique index.  Two NULL dedupe_keys
        -- are not considered duplicates by SQL standard so
        -- non-deduped runs co-exist cleanly.
        CREATE TABLE IF NOT EXISTS run_history (
            run_id        TEXT PRIMARY KEY,
            command       TEXT NOT NULL,
            started_at    TIMESTAMP NOT NULL,
            finished_at   TIMESTAMP,
            duration_ms   BIGINT,
            status        TEXT NOT NULL DEFAULT 'pending',
            host          TEXT,
            counters      JSON,
            error_type    TEXT,
            error_message TEXT,
            dedupe_key    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_run_history_dedupe
            ON run_history(command, dedupe_key);
        CREATE INDEX IF NOT EXISTS idx_run_history_started
            ON run_history(started_at);
        CREATE INDEX IF NOT EXISTS idx_run_history_command
            ON run_history(command);
    """,
    ),
    (
        3,
        """
        -- Migration #3: calibration records (forecast back-testing).
        -- Stores predicted-vs-actual pairs so the calibrate leg can compute
        -- MAPE (Mean Absolute Percentage Error) and auto-tune margins.
        -- The calibrate table is the "compare" half of sense→decide→act→measure→calibrate.
        CREATE TABLE IF NOT EXISTS calibrate (
            calibrate_id    TEXT PRIMARY KEY,
            sku             TEXT NOT NULL,
            ts_forecast     TIMESTAMP NOT NULL,
            ts_actual       TIMESTAMP,
            predicted_price DOUBLE NOT NULL,
            actual_price    DOUBLE,
            error_pct       DOUBLE,
            horizon_days    INTEGER NOT NULL DEFAULT 14,
            source_command  TEXT,
            recorded_at     TIMESTAMP DEFAULT current_timestamp
        );
        CREATE INDEX IF NOT EXISTS idx_calibrate_sku
            ON calibrate(sku);
        CREATE INDEX IF NOT EXISTS idx_calibrate_ts_forecast
            ON calibrate(ts_forecast);
    """,
    ),
    (
        4,
        """
        -- Migration #4: corrective actions (auto-playbook).
        -- Each row records an action the system auto-triggered when
        -- loop-report detected a degradation (e.g. "scaffold success < 30%").
        -- Over time this becomes a machine-readable playbook.
        CREATE TABLE IF NOT EXISTS corrective_actions (
            action_id     TEXT PRIMARY KEY,
            trigger_cmd   TEXT NOT NULL,
            trigger_metric TEXT NOT NULL,
            trigger_value TEXT,
            action_taken  TEXT NOT NULL,
            outcome       TEXT,
            outcome_detail TEXT,
            created_at    TIMESTAMP DEFAULT current_timestamp,
            resolved_at   TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_corrective_trigger
            ON corrective_actions(trigger_cmd, created_at);
        CREATE INDEX IF NOT EXISTS idx_corrective_outcome
            ON corrective_actions(outcome);
    """,
    ),
    (
        5,
        """
        -- Migration #5: trend_history — immutable ledger of product classifications.
        -- Every run of classify_all appends a snapshot here so we can look back
        -- at past trends, detect multi-window patterns (breakout, cooling, etc.),
        -- and surface them in the live trend report.
        -- timeframe_days distinguishes daily (1), weekly (7), 2-week (14),
        -- monthly (30), and quarterly (90) windows for the same product.
        CREATE TABLE IF NOT EXISTS trend_history (
            source             TEXT NOT NULL,
            external_id        TEXT NOT NULL,
            title              TEXT,
            status             TEXT,
            slope_pct_per_day  DOUBLE,
            n_points           INTEGER,
            window_start       TIMESTAMP,
            window_end         TIMESTAMP NOT NULL,
            timeframe_days     INTEGER NOT NULL,
            recorded_at        TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (source, external_id, timeframe_days, window_end)
        );
        CREATE INDEX IF NOT EXISTS idx_trend_history_ext
            ON trend_history(source, external_id);
        CREATE INDEX IF NOT EXISTS idx_trend_history_window
            ON trend_history(window_end);
        CREATE INDEX IF NOT EXISTS idx_trend_history_tf
            ON trend_history(timeframe_days);
    """,
    ),
    (
        6,
        """
        -- Migration #6: cross_timeframe_patterns — latest cross-timeframe pattern
        -- per product. Used to detect transitions into breakout/accelerating and
        -- to avoid duplicate alerts. Updated idempotently by classify_all.
        CREATE TABLE IF NOT EXISTS cross_timeframe_patterns (
            source            TEXT NOT NULL,
            external_id       TEXT NOT NULL,
            title             TEXT,
            pattern           TEXT NOT NULL,
            status            TEXT,
            slope_pct_per_day DOUBLE,
            window_end        TIMESTAMP,
            detected_at       TIMESTAMP DEFAULT current_timestamp,
            alerted_at        TIMESTAMP,
            PRIMARY KEY (source, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cross_tf_pattern
            ON cross_timeframe_patterns(pattern);
        CREATE INDEX IF NOT EXISTS idx_cross_tf_alerted
            ON cross_timeframe_patterns(alerted_at);
    """,
    ),
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
        """Try to grab an FCNTL writer flock with exponential+ jitter backoff.

        **Why no PID-stamping or live-dead detection?**
        FCNTL ``flock`` is released by the kernel **immediately** when the
        owning process exits (all file descriptors are closed on process
        death).  There is no such thing as a "stale FCNTL flock" — the
        kernel guarantees the lock is released.  PID-stamping is therefore
        unnecessary **and harmful**: a recycled PID makes ``os.kill(pid, 0)``
        return True for an unrelated process, causing a false-positive
        "live writer" stall.

        The backoff simply retries until the prior holder's fd is reclaimed.
        """
        import random as _random
        import time as _time

        lock_path = self.db_path.parent / f".{self.db_path.name}.writer.lock"
        last_err: OSError | None = None
        for base_ms in self._LOCK_BACKOFF_MS:
            jitter_ms = _random.uniform(0, 10)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            self._lock_fh = os.fdopen(fd, "r+b")
            try:
                fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                last_err = e
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    self._lock_fh.close()
                    self._lock_fh = None
                    raise
                self._lock_fh.close()
                self._lock_fh = None
                _time.sleep((base_ms + jitter_ms) / 1000.0)
            else:
                return

        if last_err is not None:
            raise RuntimeError(
                f"could not acquire writer lock on {self.db_path} after "
                f"{len(self._LOCK_BACKOFF_MS)} attempts; "
                "another writer may still be running. "
                "If no other process is running, "
                "run `rm -f data/.trends.duckdb.writer.lock` and retry.",
            ) from last_err
        raise RuntimeError(f"could not acquire writer lock on {self.db_path}")

    # Backoff schedule.  FCNTL flocks are released instantly on process
    # death, so we only need to wait for a concurrent live writer to finish
    # OR for the kernel to finish closing the prior holder's fd.  Six retries
    # up to 2.6 s cover all realistic races.
    _LOCK_BACKOFF_MS: tuple[int, ...] = (50, 100, 200, 400, 800, 1000)

    # ── connection ──────────────────────────────────────────────────────────
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self.read_only:
            import time as _time
            # Retry a few times — DuckDB blocks read-only connections while a
            # writer holds the DB open. The scheduler's writer window is brief
            # (a few seconds), so a short backoff is almost always enough.
            for attempt in range(10):
                try:
                    return duckdb.connect(str(self.db_path), read_only=True)
                except duckdb.IOException as e:
                    if "Could not set lock" in str(e) and attempt < 9:
                        _time.sleep(0.5)
                        continue
                    raise
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
            rows = (
                self.conn()
                .execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = ? ORDER BY ordinal_position",
                    [table],
                )
                .fetchall()
            )
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
