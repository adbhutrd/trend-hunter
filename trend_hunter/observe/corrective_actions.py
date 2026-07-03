"""Corrective actions — auto-playbook for when the feedback loop detects degradation.

Each row records a machine-triggered action so the system builds a playbook
over time.  The ``loop-report`` CLI auto-suggests corrective actions when
success rates drop below threshold.

Usage::

    from trend_hunter.observe.corrective_actions import record_action

    record_action(storage, trigger_cmd="scaffold",
                  trigger_metric="success_rate",
                  trigger_value="0.3",
                  action_taken="rotated proxy; re-ran scaffold")
"""

from __future__ import annotations

import uuid
from typing import Any


def record_action(
    storage: Any,
    *,
    trigger_cmd: str,
    trigger_metric: str,
    trigger_value: str,
    action_taken: str,
    outcome: str | None = "pending",
    outcome_detail: str | None = None,
) -> str:
    """Record a corrective action.  Returns the ``action_id``."""
    action_id = str(uuid.uuid4())
    storage.execute(
        "INSERT INTO corrective_actions "
        "(action_id, trigger_cmd, trigger_metric, trigger_value, "
        " action_taken, outcome, outcome_detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            action_id,
            trigger_cmd,
            trigger_metric,
            trigger_value,
            action_taken,
            outcome,
            outcome_detail,
        ),
    )
    return action_id


def resolve_action(
    storage: Any,
    action_id: str,
    outcome: str,
    outcome_detail: str | None = None,
) -> None:
    """Mark a corrective action as resolved (or failed)."""
    storage.execute(
        "UPDATE corrective_actions SET outcome = ?, outcome_detail = ?, "
        "resolved_at = current_timestamp WHERE action_id = ?",
        (outcome, outcome_detail, action_id),
    )


def recent_actions(
    storage: Any,
    days: int = 7,
    outcome: str | None = None,
) -> list[dict]:
    """Fetch recent corrective actions, optionally filtered by outcome."""
    if outcome:
        return storage.query(
            f"""
            SELECT * FROM corrective_actions
            WHERE created_at >= now() - INTERVAL {int(days)} DAY
              AND outcome = ?
            ORDER BY created_at DESC
            """,
            (outcome,),
        )
    return storage.query(
        f"""
        SELECT * FROM corrective_actions
        WHERE created_at >= now() - INTERVAL {int(days)} DAY
        ORDER BY created_at DESC
        """,
    )


def auto_playbook_suggestions(storage: Any, days: int = 30) -> list[dict]:
    """Return the most effective corrective actions grouped by trigger.

    Useful for ``loop-report`` to show "when scaffold fails, do X — it worked
    Y% of the time."  Builds the playbook automatically from history.
    """
    rows = storage.query(
        f"""
        SELECT trigger_cmd, trigger_metric, action_taken, outcome,
               count(*) AS times_used
        FROM corrective_actions
        WHERE created_at >= now() - INTERVAL {int(days)} DAY
        GROUP BY trigger_cmd, trigger_metric, action_taken, outcome
        ORDER BY trigger_cmd, times_used DESC
        """,
    )
    return rows


def auto_trigger(
    storage: Any,
    *,
    trigger_cmd: str,
    trigger_metric: str = "success_rate",
    threshold: float = 0.3,
    default_action: str | None = None,
) -> str | None:
    """Check if *trigger_cmd*\\'s success rate is below *threshold*.

    If so, record a corrective action with *default_action* (or a generic
    description) and return the ``action_id``.  Returns ``None`` if the
    rate is healthy.
    """
    from trend_hunter.observe.run_ledger import success_rate

    rate = success_rate(storage, trigger_cmd, days=1)
    if rate >= threshold:
        return None

    action = default_action or (
        f"auto-detected: {trigger_cmd} success rate = {rate * 100:.0f}% "
        f"(threshold {threshold * 100:.0f}%); "
        "manual investigation recommended"
    )
    return record_action(
        storage,
        trigger_cmd=trigger_cmd,
        trigger_metric=trigger_metric,
        trigger_value=f"{rate * 100:.0f}%",
        action_taken=action,
        outcome="pending",
        outcome_detail=f"rate dropped below {threshold * 100:.0f}% threshold",
    )
