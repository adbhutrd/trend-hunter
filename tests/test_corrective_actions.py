"""Tests for the corrective_actions module — auto-playbook recorder.

Uses the shared ``storage`` fixture.
"""

from __future__ import annotations

from trend_hunter.observe.corrective_actions import (
    auto_playbook_suggestions,
    auto_trigger,
    recent_actions,
    record_action,
    resolve_action,
)
from trend_hunter.observe.run_ledger import record_run


def test_record_action_returns_id(storage):
    """record_action inserts a row and returns the action_id."""
    aid = record_action(
        storage,
        trigger_cmd="scaffold",
        trigger_metric="success_rate",
        trigger_value="0.3",
        action_taken="rotated proxy",
    )
    assert isinstance(aid, str)
    assert len(aid) > 0


def test_record_action_persists(storage):
    """The inserted row is queryable from the corrective_actions table."""
    aid = record_action(
        storage,
        trigger_cmd="scan",
        trigger_metric="error_rate",
        trigger_value="0.5",
        action_taken="restarted scraper",
        outcome="pending",
    )
    rows = storage.query(
        "SELECT action_id, trigger_cmd, trigger_metric, action_taken, outcome "
        "FROM corrective_actions WHERE action_id = ?",
        (aid,),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["action_id"] == aid
    assert row["trigger_cmd"] == "scan"
    assert row["action_taken"] == "restarted scraper"
    assert row["outcome"] == "pending"


def test_resolve_action_marks_resolved(storage):
    """resolve_action sets outcome and resolved_at."""
    aid = record_action(
        storage,
        trigger_cmd="scaffold",
        trigger_metric="success_rate",
        trigger_value="0.2",
        action_taken="rotated proxy",
        outcome="pending",
    )
    resolve_action(storage, aid, outcome="resolved", outcome_detail="proxy rotated successfully")

    rows = storage.query(
        "SELECT outcome, outcome_detail, resolved_at FROM corrective_actions WHERE action_id = ?",
        (aid,),
    )
    assert rows[0]["outcome"] == "resolved"
    assert rows[0]["outcome_detail"] == "proxy rotated successfully"
    assert rows[0]["resolved_at"] is not None


def test_recent_actions_returns_all(storage):
    """recent_actions returns all rows within the lookback window."""
    record_action(
        storage, trigger_cmd="scan", trigger_metric="err", trigger_value="1", action_taken="fix"
    )
    record_action(
        storage, trigger_cmd="run", trigger_metric="err", trigger_value="1", action_taken="fix2"
    )

    rows = recent_actions(storage, days=7)
    assert len(rows) >= 2


def test_recent_actions_filters_by_outcome(storage):
    """recent_actions filters by outcome when given."""
    record_action(
        storage,
        trigger_cmd="scan",
        trigger_metric="err",
        trigger_value="1",
        action_taken="a",
        outcome="resolved",
    )
    record_action(
        storage,
        trigger_cmd="scan",
        trigger_metric="err",
        trigger_value="1",
        action_taken="b",
        outcome="failed",
    )

    resolved = recent_actions(storage, days=7, outcome="resolved")
    assert all(r["outcome"] == "resolved" for r in resolved)


def test_auto_playbook_suggestions_empty(storage):
    """auto_playbook_suggestions returns empty list when no actions exist."""
    suggestions = auto_playbook_suggestions(storage, days=7)
    assert isinstance(suggestions, list)
    assert len(suggestions) == 0


def test_auto_playbook_suggestions_groups(storage):
    """auto_playbook_suggestions groups by (trigger_cmd, action_taken, outcome)."""
    record_action(
        storage,
        trigger_cmd="scaffold",
        trigger_metric="sr",
        trigger_value="0.3",
        action_taken="rotate proxy",
        outcome="resolved",
    )
    record_action(
        storage,
        trigger_cmd="scaffold",
        trigger_metric="sr",
        trigger_value="0.3",
        action_taken="rotate proxy",
        outcome="resolved",
    )
    record_action(
        storage,
        trigger_cmd="scaffold",
        trigger_metric="sr",
        trigger_value="0.3",
        action_taken="restart vpn",
        outcome="failed",
    )

    suggestions = auto_playbook_suggestions(storage, days=7)
    assert len(suggestions) == 2  # 2 distinct groups


def test_auto_trigger_noop_when_healthy(storage):
    """auto_trigger returns None when success rate >= threshold."""
    # Create 2 successful runs → 100% success rate
    for _ in range(2):
        with record_run("arbitrage", storage=storage):
            pass
    # 100% >= 50% → healthy, no action triggered
    result = auto_trigger(storage, trigger_cmd="arbitrage", threshold=0.5)
    assert result is None


def test_auto_trigger_creates_action_on_low_rate(storage):
    """auto_trigger records a corrective action when rate is below threshold."""
    # No runs exist → success_rate is 0% → below threshold 0.5
    aid = auto_trigger(
        storage, trigger_cmd="scaffold", threshold=0.5, default_action="investigate immediately"
    )
    assert aid is not None

    rows = storage.query(
        "SELECT action_id, trigger_cmd, action_taken FROM corrective_actions WHERE action_id = ?",
        (aid,),
    )
    assert len(rows) == 1
