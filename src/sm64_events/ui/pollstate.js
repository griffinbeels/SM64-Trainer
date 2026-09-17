import { getJSON } from "./api.js";

// A slow/offline server must not turn the five-second status poll into an
// unbounded collection of pending requests. Retain the last known state while
// unavailable, then retry after the preceding request has released.
export function pollJSON(url, changed, { intervalMs = 5000, timeoutMs = 10000, onError } = {}) {
  let stopped = false, timer = null, request = null;
  async function poll() {
    request = new AbortController();
    const current = request;
    const deadline = setTimeout(() => current.abort(), timeoutMs);
    try {
      const state = await getJSON(url, { signal: current.signal });
      if (!stopped && !current.signal.aborted) changed(state);
    } catch (error) {
      // A failed observation supplies no new pause truth. The next poll retries.
      if (!stopped) onError?.(error);
    } finally {
      clearTimeout(deadline);
      if (!stopped) timer = setTimeout(poll, intervalMs);
    }
  }
  poll();
  return () => { stopped = true; clearTimeout(timer); request?.abort(); };
}
