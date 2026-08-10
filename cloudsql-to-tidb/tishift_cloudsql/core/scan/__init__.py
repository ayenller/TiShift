"""Scan phase — read-only inspection of a Cloud SQL for MySQL instance.

Split deliberately into two halves:

* `collectors/` run SQL against the live source. They own every query and every
  `pymysql.Error` degradation path, and they return plain dataclasses.
* `analyzers/` are pure functions over those dataclasses. They never touch a
  connection, which is why ~90% of the scan logic is unit-testable on dict
  fixtures with no database anywhere in the test suite.

`orchestrator.run_scan()` is the only place that sees both halves, and it is
kept thin on purpose: if you find yourself adding logic there, it belongs in an
analyzer instead.

What is deliberately *not* implemented: query-log analysis. Several rules
(BLOCKER-5/6/7, WARNING-5/6/7) can only be confirmed by inspecting application
traffic, which this tool never sees. Those rules are still registered and are
fed from a `QueryLogSignals` default of "not detected" — reports must say
"not detected", never "clear", so nobody mistakes a missing collector for a
passing check.
"""
