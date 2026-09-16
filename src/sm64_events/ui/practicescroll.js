import { useEffect, useRef } from "preact/hooks";
import { entityKey } from "./entitysection.js";
import { prefersReducedMotion } from "./useTween.js";

// Carry success identity through the same celebration hold as the selector.
// A segment's ID names its START, so freshness is membership, never a max ID.
export function practiceSuccessSnapshot(view) {
  if (!view) return null;
  const ids = new Set((view.unassigned || []).map((attempt) => attempt.id));
  const successes = [];
  for (const section of [...view.stars, ...(view.segments || [])]) {
    for (const attempt of section.attempts) {
      ids.add(attempt.id);
      if (attempt.outcome !== "success" || attempt.cleared || attempt.imported
          || attempt.journal_id == null) continue;
      successes.push({ key: entityKey(section), id: attempt.id,
        endedUtc: attempt.ended_utc || "", journalId: attempt.journal_id });
    }
  }
  successes.sort((a, b) => b.endedUtc.localeCompare(a.endedUtc)
    || b.journalId - a.journalId);
  return { sessionId: view.session?.id, scope: view.scope, clock: view.clock,
    ids, successes };
}

export function usePracticeScroll(snapshot, activeKey) {
  const previous = useRef(null);
  useEffect(() => {
    if (!snapshot) return;
    const before = previous.current;
    // Loading a page/session/scope brings history, not a newly played entry.
    if (!before || before.sessionId !== snapshot.sessionId
        || before.scope !== snapshot.scope || before.clock !== snapshot.clock) {
      previous.current = { ...snapshot,
        practiceKey: snapshot.successes[0]?.key ?? activeKey };
      return;
    }
    const success = snapshot.successes.find((row) => !before.ids.has(row.id));
    snapshot.ids.forEach((id) => before.ids.add(id));
    if (!success) return;
    const moved = before.practiceKey && success.key !== before.practiceKey;
    before.practiceKey = success.key;
    if (!moved) return;
    // Selection may precede completion. Compare the last completed entity,
    // so the first success after moving on scrolls, but repeated runs do not.
    window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "instant" : "smooth" });
  }, [snapshot, activeKey]);
}
