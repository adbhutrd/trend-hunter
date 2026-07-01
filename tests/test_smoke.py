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
    return subprocess.run(                                                    # noqa: S603
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


def test_forget_unknown_email_is_idempotent():
    """forget must not crash even when no leads match.

    Runs three times and confirms no traceback on any invocation.
    """
    args = ["forget", "nobody-abc-123@example.com"]
    for _ in range(3):
        r = _run(args)
        assert "Traceback" not in (r.stdout or "")
        assert "Traceback" not in (r.stderr or "")


def test_scan_help():
    """`scan --help` must render without a traceback; exit code 0 is the assertion."""
    r = _run(["scan", "--help"])
    assert r.returncode == 0
    assert "Traceback" not in (r.stdout or "")
    assert "Traceback" not in (r.stderr or "")
