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
export async function send(method, url, body) {
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw await httpError(url, r);
  return r.status === 204 ? null : r.json();
}
