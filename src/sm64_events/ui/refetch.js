// src/sm64_events/ui/refetch.js — the one door for "refetch because the data
// went stale" versus "refetch because you are now looking at something else".
//
// Round 20, 2026-09-01, his report: "the page... is randomly refreshing? It
// keeps refreshing without me doing anything, and I fear we have made a
// mistake somewhere that accidentally prompts this type of autorefreshing."
//
// Measured on his own live server: 90 seconds on the event socket while he
// played carried one `attempt_completed`, which is in store.js's REFRESH_ON
// set. That bumps `t.mareloRev`, and every Rank-tab fetch answered a bump by
// CLEARING its state first — `setData(null)` — so the scorecard blanked to
// "Loading your scorecard…" and the rating, chart and breakdown dropped to
// their inline loading states, about once a minute while he practised, with
// no gesture of his anywhere on that page.
//
// The clear was never for the staleness key. It is there so a 404 on a NEW
// scope cannot leave the OLD scope's rows sitting under the new scope's
// label — a scope switch genuinely must not show the previous scope's
// numbers. Clearing on BOTH is what produced the flicker, so this splits
// them: an identity change clears and refetches, a staleness bump refetches
// in place and swaps the answer in when it lands.
//
// One module because three surfaces need the same rule (the scorecard, the
// Rank page's own fetches, the leaderboard) and each had its own copy of the
// clear-then-fetch idiom. The pure half — `identityChanged` — lives here too
// so a node test can drive the decision without a browser.
import { useEffect, useRef } from "preact/hooks";

// A sentinel distinct from every real identity INCLUDING null/undefined: the
// first run must count as a change (nothing is on screen to preserve), and a
// scope of `null` is a real value the Rank page holds before its scopes load.
const NOTHING = Symbol("no-identity-yet");

export function identityChanged(previous, next) {
  return previous !== next;
}

// `load(cleared)` runs whenever `identity` or `staleness` changes, and is
// told which of the two it was: `cleared` is true only on an identity change
// (and on the first run). It may return a cleanup, exactly like the effect it
// replaces, so the `alive` guard each caller already had keeps working.
export function useIdentityFetch(identity, staleness, load) {
  const previous = useRef(NOTHING);
  useEffect(() => {
    const cleared = identityChanged(previous.current, identity);
    previous.current = identity;
    return load(cleared);
    // `load` is deliberately not a dependency: it closes over this render's
    // state setters, which are stable, and adding it would re-run the fetch
    // on every render — the opposite of the bug being fixed here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, staleness]);
}
