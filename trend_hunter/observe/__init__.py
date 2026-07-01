"""Loop-engineering observability — run history, decision traces, calibration.

The runtime feedback spine. Every long-lived operation writes one row to
``run_history`` via :class:`trend_hunter.observe.run_ledger.record_run`,
giving the system a single SQL source of truth for:

- Was yesterday's scan ok? (``status = 'ok'``)
- How many products does each scan ingest on average? (``counters``)
- Did Shopify killswitch trip this week?  (``error_message``)
- Is money_sweep getting more matches over time?  (``counters.rows``)
"""
