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
let localAccess = null;
async function requestToken() {
  if (!localAccess) {
    localAccess = fetch("/api/local-access", { cache: "no-store" }).then(async response => {
      // Unprotected isolated fixtures and older app servers retain their API.
      if (response.status === 404) return null;
      if (!response.ok) throw await httpError("/api/local-access", response);
      const result = await response.json();
      if (typeof result.request_token !== "string") throw new Error("Local access unavailable.");
      return result.request_token;
    }).catch(error => { localAccess = null; throw error; });
  }
  return localAccess;
}

export async function localFetch(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  if (["GET", "HEAD", "OPTIONS"].includes(method)) return fetch(url, options);
  if (new URL(url, location.href).origin !== location.origin) {
    throw new Error("Local actions must use this app's address.");
  }
  const token = await requestToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("X-SM64-Request-Token", token);
  const response = await fetch(url, { ...options, headers });
  // A restarted server owns a new capability. Let the next deliberate action
  // bootstrap again; never automatically replay a possibly accepted mutation.
  if (response.status === 403) localAccess = null;
  return response;
}

export async function getJSON(url, options) {
  const r = await localFetch(url, options);
  if (!r.ok) throw await httpError(url, r);
  return r.json();
}
// Poll a job's status URL until it leaves `running`, calling `onStep(status)`
// on every poll so a progress line can narrate it; resolves with the job's
// `result`, rejects with its `message` on `error`. The one shape every job
// door answers with (`server/jobs.py`): `{state, progress, message, result}`.
export function pollJob(statusUrl, onStep, intervalMs = 250) {
  return new Promise((resolve, reject) => {
    const tick = () => {
      getJSON(statusUrl)
        .then((status) => {
          if (onStep) onStep(status);
          if (status.state === "running") { setTimeout(tick, intervalMs); return; }
          if (status.state === "error") { reject(new Error(status.message)); return; }
          resolve(status.result);
        })
        .catch(reject);
    };
    tick();
  });
}

export async function send(method, url, body) {
  const r = await localFetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw await httpError(url, r);
  return r.status === 204 ? null : r.json();
}
