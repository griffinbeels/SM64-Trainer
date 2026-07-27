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

export async function requestTarget(t, body) {
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
  releaseCelebrationHold();
  try {
    await send("POST", "/api/target", body);
    t.refresh();
    return true;
  } catch (refusal) {
    t.setNotice(String(refusal.message || refusal));
    return false;
  }
}
