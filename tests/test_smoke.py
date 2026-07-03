"""CLI smoke tests — confirm CLI commands don't crash with tracebacks.

The subprocess cwd is the project root (parent of this file), so the CLI can
import `trend_hunter` regardless of where pytest is invoked from.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "trend_hunter.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=False,
    )


def test_help():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "trend-hunter" in r.stdout


def test_stats_on_empty_db():
    """With no DB file, stats should not crash with a traceback."""
    r = _run(["stats"])
    assert "Traceback" not in (r.stdout or "")
    assert "Traceback" not in (r.stderr or "")


def test_forget_unknown_email_is_idempotent(tmp_path):
    """forget must not crash even when no leads match.

    Uses a dedicated ``tmp_path`` DuckDB so it never contends with other
    tests for the real FCNTL writer lock.  Runs three times to confirm
    idempotency; each invocation opens a fresh writer on the same DB.
    """
    import os

    db = tmp_path / "test.duckdb"
    lock = tmp_path / ".test.duckdb.writer.lock"

    for _ in range(3):
        # Each iteration opens writer → forget → closes writer, all
        # pointing to the tmp_path DB.  Use subprocess to match the
        # other smoke tests, but reset env so it hits tmp_path.
        env = {**os.environ, "TH_DB_PATH": str(db)}
        r = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "trend_hunter.cli", "forget", "nobody-abc-123@example.com"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            env=env,
            check=False,
        )
        assert "Traceback" not in (r.stdout or "")
        assert "Traceback" not in (r.stderr or "")
        # Lock file should be cleanable after each run.
        assert lock.exists() is False or lock.stat().st_size == 0
    # Clean up.
    if lock.exists():
        lock.unlink()


def test_scan_help():
    """`scan --help` must render without a traceback; exit code 0 is the assertion."""
    r = _run(["scan", "--help"])
    assert r.returncode == 0
    assert "Traceback" not in (r.stdout or "")
    assert "Traceback" not in (r.stderr or "")
