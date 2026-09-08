// Review state belongs to the server's replay lifetime, not browser storage.
import { useEffect, useState } from "preact/hooks";
import { retainDraft, readDraft, acknowledgeDraft } from "./reviewdraft.js";

const entries = new Map();
const endpoint = id => `/api/attempts/${id}/replay/review-state`;

function takeWrite(entry) {
  const snapshot = { state: entry.state, edit: entry.edit };
  entry.dirty = false;
  return snapshot;
}

function finishWrite(entry, id, edit) {
  entry.error = null;
  acknowledgeDraft(id, edit);
}

function failWrite(entry, error, notify) {
  entry.dirty = !error.sessionChanged;
  if (error.sessionChanged) {
    // The server discarded this session. Retry/reopen must GET its new token
    // and saved state, never retry an old draft under the new lifetime.
    entry.state = null; entry.session = null; entry.edit = null;
  }
  entry.error = String(error); notify();
}

async function rejectedWrite(response) {
  const detail = await response.json?.().catch(() => null);
  const sessionChanged = response.status === 409
    && detail?.detail === "review session changed; reopen the replay";
  return Object.assign(new Error(sessionChanged
    ? "The recording session changed. Retry to reload saved review preferences."
    : `Review changes could not be retained (${response.status}).`), { sessionChanged });
}

async function writePending(entry, id, notify) {
  while (entry.dirty) {
    const { state, edit } = takeWrite(entry);
    try {
      const response = await fetch(endpoint(id), { method: "PUT", keepalive: true,
        headers: { "Content-Type": "application/json",
          ...(edit ? { "X-Replay-Review-Edit": edit } : {}) }, body: JSON.stringify(state) });
      if (!response.ok) throw await rejectedWrite(response);
      finishWrite(entry, id, edit);
    } catch (error) {
      failWrite(entry, error, notify); throw error;
    }
  }
}

function flushEntry(entry, id, notify) {
  // Every caller awaits the same writer. Only that writer drains later edits
  // and releases ownership; waiting callers never start a competing PUT.
  if (entry.writing) return entry.writing;
  if (!entry.dirty) return Promise.resolve();
  entry.writing = writePending(entry, id, notify).finally(() => {
    entry.writing = null; notify();
    if (!entry.listeners.size && !entry.dirty && entries.get(id) === entry) entries.delete(id);
  });
  return entry.writing;
}

function entryFor(id) {
  if (entries.has(id)) return entries.get(id);
  const entry = { state: null, error: null, listeners: new Set(), dirty: false, writing: null };
  const notify = () => entry.listeners.forEach(listener => listener({ ...entry }));
  entry.load = () => fetch(endpoint(id)).then(async response => {
    if (!response.ok) throw new Error(`Review preferences could not be loaded (${response.status}).`);
    const state = await response.json();
    entry.session = response.headers?.get?.("X-Replay-Review-Session") || null;
    const draft = readDraft(id, entry.session);
    entry.state = draft?.state || state; entry.error = null;
    entry.edit = draft?.edit || null; entry.dirty = !!draft; notify();
    if (draft) entry.flush().catch(() => {});
  }).catch(error => { entry.error = String(error); notify(); });
  entry.loading = entry.load();
  entry.flush = () => flushEntry(entry, id, notify);
  entry.change = patch => {
    if (!entry.state) return;
    entry.state = { ...entry.state, ...patch,
      template_offsets: { ...entry.state.template_offsets, ...patch.template_offsets } };
    entry.edit = retainDraft(id, entry.session, entry.state);
    entry.dirty = true; notify();
    entry.flush().catch(() => {});
  };
  entries.set(id, entry);
  return entry;
}

export function useReviewState(attemptId) {
  const [snapshot, setSnapshot] = useState({ state: null, error: null });
  useEffect(() => {
    const entry = entryFor(attemptId);
    entry.listeners.add(setSnapshot); setSnapshot({ ...entry });
    return () => {
      entry.listeners.delete(setSnapshot);
      if (!entry.writing && !entry.dirty) entries.delete(attemptId);
    };
  }, [attemptId]);
  return { state: snapshot.state, error: snapshot.error,
    change: patch => entryFor(attemptId).change(patch),
    flush: () => entryFor(attemptId).flush(),
    retry: () => {
      const entry = entryFor(attemptId);
      return entry.state ? entry.flush() : entry.load();
    } };
}
