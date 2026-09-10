# Prototype execution

Priority update: task 0136 requires an attributable performance baseline before further continuous replay preparation work. See [the profiling guide](../../docs/profiling.md) for the tooling and measurement sequence, and [replay review](../../docs/replay-review.md) for measured improvements and remaining limits.

Foundation: this ownership/contract record, reviewed against existing service, timeline, transport, and drawer.

Parallel prototype wave (each writer has its own checkout):

- Backend track owns `inputs/service.py`, `replay/reviewstate.py` (new), `replay/service.py`, `server/replay_api.py`, and their focused Python tests. Add a backward-compatible `template.source` object with unclipped zero-origin runs/actions, `frames`, and content `revision`. Add GET/PUT `/api/attempts/{id}/replay/review-state` for `{template_offsets: {"<template-id>:<revision>": integer}, zoom: {start, end}|null, loop: {start, end, enabled}|null}`. Zoom coordinates are input-axis frames; loop coordinates are presented media seconds. GET returns valid empty state when none. State follows replay lifetime, survives temporary drawer/refresh and saved server restart; successful save promotes it; edits after save persist without reencoding. Validate finite, bounded fields. Existing replay behavior stays compatible.
- Timeline track owns `ui/components/inputtimeline.js`, a new `ui/timelinereview.js` helper if useful, and `tests/frontend/inputtimeline*.test.js`. Props `reviewState`, `onReviewState(patch)` and `onLoopChange` are supplied by the drawer. Template offsets key on `<id>:<source.revision>`. Full source data comes through the additive field above, with existing payload fallback. Add shift handle/nudges/reset, anchored zoom/Fit/Zoom to loop, static geometry caching. Source template moves; actual data/IGT/loop stay fixed. No approximation for unmapped/ambiguous media. Root owns common CSS and drawer wiring.
- Root owns shared transport/player/downloaded integration, review-state hook and drawer, contained playback shortcuts, CSS, isolated browser fixture, and integration. Existing exact stepping helpers are preserved. Full visual and focused behavioral checks on the combined tree; root reviews every returned diff.

Continuous-preparation work is independent read-only analysis/probe until this usable review interface is reachable. Do not start the live recorder. Do not claim browser-focus shortcuts work in Project64.

No full suite per worker: use the shared test runner for targeted serial checks. The integrated tree owns the whole-suite gate when ready for hardening/integration. Record prototype and human feedback before advancing to engineering hardening.
