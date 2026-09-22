import { getJSON } from "./api.js";

// A slow/offline server must not turn the five-second status poll into an
// unbounded collection of pending requests. Retain the last known state while
// unavailable, then retry after the preceding request has released.
// `intervalMs` may be a function of the last state received (null before the
// first): a job list is worth two looks a second while it moves and one every
// few seconds while it does not.
export function pollJSON(url, changed, { intervalMs = 5000, timeoutMs = 10000, onError } = {}) {
  let stopped = false, timer = null, request = null, last = null;
  async function poll() {
    request = new AbortController();
    const current = request;
    const deadline = setTimeout(() => current.abort(), timeoutMs);
    try {
      const state = await getJSON(url, { signal: current.signal });
      if (!stopped && !current.signal.aborted) { last = state; changed(state); }
    } catch (error) {
      // A failed observation supplies no new pause truth. The next poll retries.
      if (!stopped) onError?.(error);
    } finally {
      clearTimeout(deadline);
      const waitMs = typeof intervalMs === "function" ? intervalMs(last) : intervalMs;
      if (!stopped) timer = setTimeout(poll, waitMs);
    }
  }
  poll();
  return () => { stopped = true; clearTimeout(timer); request?.abort(); };
}
