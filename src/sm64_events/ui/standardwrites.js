// Order edits and clears from one mounted standards editor. HTTP arrival
// order otherwise lets an earlier field blur overwrite the player's last one.
import { h } from "preact";
import { useRef, useState } from "preact/hooks";
import htm from "htm";
import { send } from "./api.js";

const html = htm.bind(h);

export function useStandardWrites(entity, load, onChanged, requestRef) {
  const [errors, setErrors] = useState({});
  const queue = useRef(Promise.resolve());
  const pending = useRef(0);
  const jpWrites = useRef(new Map());
  const sequence = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  function report(key, error, operation) {
    setErrors((prev) => {
      const next = { ...prev };
      if (error) next[key] = { message: error.message || String(error), operation };
      else delete next[key];
      return next;
    });
  }
  async function mutate(method, path, body, onSaved = () => {}) {
    const operation = ++sequence.current;
    pending.current += 1;
    requestRef.current += 1;
    const write = queue.current.then(() => send(method, path, body));
    queue.current = write.catch(() => {});
    try {
      const result = await write;
      report(path, null);
      onSaved(operation);
      return result;
    } catch (err) {
      report(path, err, operation);
      throw err;
    } finally {
      pending.current -= 1;
      if (!pending.current) {
        // A failed read is distinct from a failed write. Notify other readers
        // even if this panel cannot yet display the successfully saved value.
        try { await loadRef.current(); report("refresh", null); }
        catch (err) { report("refresh", new Error(`Saved standards could not be refreshed: ${err.message || err}`)); }
        onChanged && onChanged();
      }
    }
  }
  function put(strat, rank, seconds, version = "us") {
    const enc = encodeURIComponent;
    if (version === "jp") jpWrites.current.set(JSON.stringify([entity, strat]), sequence.current + 1);
    const qs = version === "jp" ? "?version=jp" : "";
    const path = `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}${qs}`;
    // Errors are rendered here; a blur has no promise-consuming caller.
    return mutate("PUT", path, { seconds }).catch(() => {});
  }
  function clear(method, strat = null, version = null, purge = false) {
    const base = `/api/ranks/standards/${encodeURIComponent(entity)}`;
    const prefix = strat == null ? base : `${base}/${encodeURIComponent(strat)}`;
    const path = method === "POST" ? `${base}/reset`
      : prefix + (version === "jp" ? "/jp" : purge ? "?purge=true" : "");
    return mutate(method, path, undefined, (through) => {
      setErrors((prev) => Object.fromEntries(Object.entries(prev).filter(([key, row]) =>
        !(row.operation <= through && key.startsWith(prefix + "/")
          && (version !== "jp" || key.endsWith("?version=jp"))))));
      for (const [key, operation] of jpWrites.current) {
        const [owner, strategy] = JSON.parse(key);
        if (operation <= through && owner === entity && (strat == null || strategy === strat))
          jpWrites.current.delete(key);
      }
    });
  }
  return { put, mutate, pending, reset: () => clear("POST"),
    clearJp: (strat) => clear("DELETE", strat, "jp"),
    clearStrategy: (strat, purge) => clear("DELETE", strat, null, purge),
    hasJpWrite: (strat) => jpWrites.current.has(JSON.stringify([entity, strat])),
    message: Object.entries(errors).map(([key, row]) =>
      html`<p key=${key} role="alert">${row.message}</p>`) };
}
