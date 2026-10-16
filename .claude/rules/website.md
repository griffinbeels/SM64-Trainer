---
paths:
  - "website/**"
  - "tools/*website.py"
  - "tests/test_*website.py"
  - ".github/workflows/website.yml"
  - "docs/website-deployment.md"
---

# Public website — editing, preview and publishing routes

Read [website/README.md](../../website/README.md) for the edit map, safe offline
preview, HTML/CSP hooks, screenshots and focused review. Website widths are
defined there; the Trainer UI's 850px minimum applies to its own source tree.

Read [the deployment runbook](../../docs/website-deployment.md) for real release
selection, CI settings, withdrawal and domain work. Keep the guide canonical;
do not reproduce the runbook or its changing readiness snapshot here.

Use the safe preview helper for design iteration. Production keeps the validated
live-release path and policy floor. Preserve the existing authentication service.
