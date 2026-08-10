// src/sm64_events/ui/target.js — ONE door for "make this the practice target".
//
// Every surface that moves the target posts the same body to the same endpoint,
// and since 2026-07-27 that endpoint can REFUSE: you may only practice what you
// are standing in front of (tracking/practicable.py). The refusal is a 409
// carrying a sentence the server already wrote — "you can only practice what you
// are standing in — that one is in Whomp's Fortress" — which names both the
// problem and the fix, so no caller composes its own wording.
//
// Routing every call through here is what stops a refusal being invisible: a
// bare `await send(...)` inside a click handler rejects into nothing, and the
// button reads as dead. Callers that need to stay open on failure read the
// boolean; the rest can ignore it.
import { send } from "./api.js";
import { releaseCelebrationHold } from "./rankclimb.js";

// `quiet` is for a write NO GESTURE ASKED FOR — today only the lone-route
// auto-pick (loneoption.js). It changes two things, and both follow from the
// same fact rather than from taste:
//   - no notice on refusal. The server's sentence answers "why did my click
//     do nothing"; raised at nobody it is a message about an action he never
//     took, which he reads as a bug in itself (2026-08-01, the replayed
//     celebration: "it feels like a bug (because I didn't trigger it)").
//   - no celebration release. Beating the rank-up HOLD is explicitly a
//     PLAYER's privilege ("if I click on another star to target while ranking
//     up, it should let me immediately jump"); a convenience pick is not a
//     click and must not cut a celebration short.
// `auto` tells the SERVER the same thing `quiet` tells the client: no gesture
// asked for this write. The projector holds an auto-filled target by round
// 19's detection rules (complete / forfeit / expire) instead of a click's
// sovereign hold, and drops a fill that races a promoted detection. It
// defaults from `quiet` — a quiet write is by definition gestureless — and
// the non-quiet convenience sites (the arena row, the Bowser family memory)
// pass it explicitly.
export async function requestTarget(t, body, { quiet = false,
                                               auto = quiet } = {}) {
  if (auto) body = { ...body, auto: true };
  // A gesture beats the rank-up HOLD (ui/rankclimb.js). While a rank is
  // climbing the practice page is frozen so the GAME cannot move it out from
  // under the celebration -- walking out of the stage the instant you grab
  // the star is the normal way to end a run. It was never meant to stop the
  // PLAYER: "if I click on another star to target while ranking up, it should
  // let me immediately jump to the other star" (2026-07-27).
  //
  // Here rather than in api.js, where it started life: this is the one door
  // every target write already goes through, so the release is explicit
  // instead of matched on a URL -- and matching the URL in api.js was a second
  // source of truth for "which path moves the target", which
  // tests/test_single_source.py rightly rejects.
  if (!quiet) releaseCelebrationHold();
  try {
    await send("POST", "/api/target", body);
    t.refresh();
    return true;
  } catch (refusal) {
    if (!quiet) t.setNotice(String(refusal.message || refusal));
    return false;
  }
}
