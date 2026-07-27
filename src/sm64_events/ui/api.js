// src/sm64_events/ui/api.js — thin fetch wrappers for /api/*
// Failures surface the server's `detail` message (the api.py error
// taxonomy: "name is required", "unknown trigger type…") — a bare
// "409" hides the explanation the server already wrote.
async function httpError(url, r) {
  let detail = null;
  try { detail = (await r.json()).detail; } catch { /* non-JSON body */ }
  const err = detail == null
    ? new Error(`${url}: ${r.status}`)
    : new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  // Callers need to tell "this row is gone" (404) from "the server blinked"
  // (network drop, restart) — the first should forget a stale id, the second
  // must NOT throw away the user's selection. The message alone can't
  // distinguish them, so carry the status.
  err.status = r.status;
  return err;
}
export async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw await httpError(url, r);
  return r.json();
}
// Moving the practice target is always a USER GESTURE -- nothing in this app
// re-targets on its own -- and a gesture beats the rank-up hold: the hold
// exists so the GAME cannot move the page mid-celebration, never to stop the
// player clicking a different star (live report 2026-07-27, "it should let me
// immediately jump to the other star"). Done HERE rather than at the ten
// call sites that POST this path, so a new one cannot forget.
const TARGET_PATHS = ["/api/target", "/api/target/pending"];

export async function send(method, url, body) {
  if (TARGET_PATHS.includes(url)) {
    const { releaseCelebrationHold } = await import("./rankclimb.js");
    releaseCelebrationHold();
  }
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw await httpError(url, r);
  return r.status === 204 ? null : r.json();
}
