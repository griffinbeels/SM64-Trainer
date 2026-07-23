// src/sm64_events/ui/api.js — thin fetch wrappers for /api/*
// Failures surface the server's `detail` message (the api.py error
// taxonomy: "name is required", "unknown trigger type…") — a bare
// "409" hides the explanation the server already wrote.
async function httpError(url, r) {
  let detail = null;
  try { detail = (await r.json()).detail; } catch { /* non-JSON body */ }
  if (detail == null) return new Error(`${url}: ${r.status}`);
  return new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
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
