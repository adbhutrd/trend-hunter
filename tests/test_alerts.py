"""Tests for the alerts module — notifications and degradation detection.

Uses the shared ``storage`` fixture so tests share the same FCNTL-lock
contract as every other write-path test.
"""
from __future__ import annotations

import pytest

from trend_hunter.observe.alerts import (
    alert_on_degradation,
    maybe_alert_on_success_rate,
    notify,
)
from trend_hunter.observe.run_ledger import record_run

# ── success-rate checks ────────────────────────────────────────────────────────


def test_maybe_alert_returns_rate(storage):
    """With no run_history, maybe_alert_on_success_rate returns 0.0 (no crash)."""
    rate = maybe_alert_on_success_rate(storage, "scaffold", days=1, threshold=0.5)
    assert rate == 0.0


def test_maybe_alert_healthy_does_not_alert(storage):
    """When rate >= threshold, the function returns the rate silently."""
    with record_run("scan", storage=storage):
        pass  # successful run → status='ok'
    rate = maybe_alert_on_success_rate(storage, "scan", days=7, threshold=0.5)
    # One ok run out of one total → 100% success rate
    assert rate == 1.0


def test_alert_on_degradation_empty_db(storage):
    """alert_on_degradation returns empty list when no runs exist."""
    offenders = alert_on_degradation(storage)
    assert offenders == []


def test_alert_on_degradation_healthy(storage):
    """With all-ok runs, alert_on_degradation returns empty list."""
    for cmd in ("scan", "run", "aggregate"):
        with record_run(cmd, storage=storage):
            pass
    offenders = alert_on_degradation(storage)
    assert offenders == []


def test_alert_on_degradation_detects_bad_rate(storage):
    """alert_on_degradation flags commands with rate between 0 and 50% in last 24h."""
    # Create 2 successful + 1 failed → rate = 2/3 ≈ 67% (not flagged)
    # Create 1 successful + 3 failed → rate = 1/4 = 25% (flagged)
    for _ in range(2):
        with record_run("scaffold", storage=storage):
            pass
    for _ in range(3):
        try:
            with record_run("scaffold", storage=storage):
                raise RuntimeError("fail")
        except RuntimeError:
            pass
    # Success rate is 2/5 = 40% → should be flagged (< 50%, > 0%)
    offenders = alert_on_degradation(storage)
    flagged = [o for o in offenders if o["command"] == "scaffold"]
    assert len(flagged) == 1
    assert flagged[0]["rate"] == pytest.approx(0.4, abs=0.01)


# ── notify is a no-op without credentials ─────────────────────────────────────


def test_notify_noop_without_credentials():
    """notify() should not crash when no alert channels are configured."""
    # No env vars set → notify is a silent no-op
    notify("test message")  # should not raise
    notify("test", discord_webhook=None)  # explicit None
