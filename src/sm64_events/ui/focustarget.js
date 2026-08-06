// src/sm64_events/ui/focustarget.js
//
// Whose history the analysis card and the detail drawer are drawing.
//
// Clicking a practice-log card puts those two surfaces into a BROWSE mode --
// a spring-loaded one, which exits itself rather than needing a "back to the
// active target" control nobody would find. Griffin's rule (2026-08-03): "the
// second we start playing again in LLL (via a reset / star grab), or through
// warping / basically anything that would trigger changing the star/segment
// selector, that new area or star or segment should take ownership of that
// card (instead of it being sticky once clicked)."
//
// The three signals ARE those three clauses, each compared against the value
// it held at the moment of the click:
//
//   stageKey        "warping / anything that changes the selector"
//   activeKey       the target moved under you
//   newestAttemptId "a reset / star grab" -- a new row landed
//
// Comparing a SNAPSHOT rather than subscribing to three events is what makes
// this testable without a browser and what makes a fourth signal one key here
// and one row in the test.
//
// Import-free, so tests/test_ui_focus_target.py drives it under node.

export function liveSnapshot({ activeKey, stageKey, newestAttemptId }) {
  return { activeKey: activeKey ?? null,
           stageKey: stageKey ?? null,
           newestAttemptId: newestAttemptId ?? null };
}

export function snapshotsAgree(a, b) {
  if (!a || !b) return false;
  return a.activeKey === b.activeKey
    && a.stageKey === b.stageKey
    && a.newestAttemptId === b.newestAttemptId;
}

/**
 * The entity key the analysis card should draw, or null when there is
 * nothing to draw.
 *
 * `manual` is `{ key, at }` — what was clicked, and the snapshot taken at the
 * click — or null. It survives only while the world has not moved.
 */
export function resolveFocus(manual, live) {
  if (manual && manual.key && snapshotsAgree(manual.at, live)) return manual.key;
  return live.activeKey;
}

/**
 * The newest attempt anywhere in the view.
 *
 * `journal_id`, never the raw `id`: a reattributed 100-coin attempt keeps a
 * segment-namespace id around 7.5e11 that outranks every native id forever,
 * so a raw max would report "a new attempt landed" on the very first view and
 * then never again.
 */
export function newestJournalId(view) {
  if (!view) return null;
  let newest = null;
  const consider = (a) => {
    if (a.journal_id != null && (newest == null || a.journal_id > newest))
      newest = a.journal_id;
  };
  view.stars.forEach((s) => s.attempts.forEach(consider));
  (view.segments || []).forEach((s) => s.attempts.forEach(consider));
  (view.unassigned || []).forEach(consider);
  return newest;
}
