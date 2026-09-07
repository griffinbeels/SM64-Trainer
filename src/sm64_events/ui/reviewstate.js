// Review state belongs to the server's replay lifetime, not browser storage.
import { useEffect, useState } from "preact/hooks";
import { getJSON } from "./api.js";

const entries = new Map();
const endpoint = id => `/api/attempts/${id}/replay/review-state`;
function entryFor(id) {
  if (entries.has(id)) return entries.get(id);
  const entry = { state: null, error: null, listeners: new Set(), dirty: false, writing: null };
  const notify = () => entry.listeners.forEach(listener => listener({ ...entry }));
  entry.load = () => getJSON(endpoint(id)).then(state => {
    entry.state = state; entry.error = null; notify();
  }).catch(error => { entry.error = String(error); notify(); });
  entry.loading = entry.load();
  entry.flush = async () => {
    if (entry.writing) await entry.writing;
    if (!entry.dirty) return;
    entry.writing = (async () => {
      while (entry.dirty) {
        const state = entry.state;
        entry.dirty = false;
        try {
          const response = await fetch(endpoint(id), { method: "PUT", keepalive: true,
            headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
          if (!response.ok) throw new Error(`Review changes could not be retained (${response.status}).`);
          entry.error = null;
        } catch (error) {
          entry.dirty = true; entry.error = String(error); notify(); throw error;
        }
      }
    })();
    try { await entry.writing; } finally {
      entry.writing = null; notify();
      if (!entry.listeners.size && !entry.dirty) entries.delete(id);
    }
  };
  entry.change = patch => {
    if (!entry.state) return;
    entry.state = { ...entry.state, ...patch,
      template_offsets: { ...entry.state.template_offsets, ...patch.template_offsets } };
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
