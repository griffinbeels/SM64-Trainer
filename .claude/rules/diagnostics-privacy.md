---
paths:
  - "src/sm64_events/core/diagnostic*.py"
  - "src/sm64_events/core/native_diagnostics.py"
  - "src/sm64_events/core/silent_failures.py"
  - "src/sm64_events/core/error_reporting*.py"
  - "src/sm64_events/core/logging_setup.py"
  - "src/sm64_events/core/perfmon.py"
  - "src/sm64_events/core/identity_privacy.py"
---
# Private failure diagnostics

[Error reporting](../../docs/error-reporting.md) owns the evidence inventory,
privacy boundaries and retention. `diagnostic_privacy.safe_snapshot` is the
automatic schema at capture, transport and reload; arbitrary native text and
gameplay/account/process-list breadcrumbs never enter it. The passive native
reader uses only observed installation paths and retains failed handle owners.
Collection never acquires capture demand. Local exports use their own redacted
projection. `tests/test_diagnostic_evidence.py` exercises actual SDK envelopes
and negative privacy cases; retention tests cover file ownership and expiry.
`identity_privacy.purge` is the last pass at every outgoing door (events,
breadcrumbs, logs, report bytes): no account or computer name leaves; a new
door calls it too, and `tests/test_identity_privacy.py` must cover it.
