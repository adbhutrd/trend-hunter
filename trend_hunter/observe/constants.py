"""Shared constants for the observe module.

Factoring these out of ``loop_report`` so ``alerts`` can import them
without creating a circular-dependency risk (loop_report ↔ alerts).

Usage::

    from trend_hunter.observe.constants import CORE_COMMANDS, COUNTER_KEYS
"""

# Commands the report treats as "core" — others go in the bottom panel.
CORE_COMMANDS = (
    "scan",
    "run",
    "aggregate",
    "arbitrage",
    "scaffold",
    "money-sweep",
    "forget",
    "validate-ads",
)

# Counters the report reads from the ``counters`` JSON column.
COUNTER_KEYS = (
    "products_in",
    "classified",
    "leads_added",
    "money_rows",
    "scaffolded",
    "audits",
    "validated",
)
