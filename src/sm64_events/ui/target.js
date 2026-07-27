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

export async function requestTarget(t, body) {
  try {
    await send("POST", "/api/target", body);
    t.refresh();
    return true;
  } catch (refusal) {
    t.setNotice(String(refusal.message || refusal));
    return false;
  }
}
