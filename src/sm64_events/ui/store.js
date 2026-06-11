// src/sm64_events/ui/store.js — session state + live WS subscription
import { useEffect, useState, useCallback } from "preact/hooks";
import { getJSON } from "./api.js";

const REFRESH_ON = new Set(["attempt_completed", "attempts_invalidated",
  "pb_saved", "session_started", "target_changed", "star_collected"]);

export function useTracker() {
  const [view, setView] = useState(null);
  const [clock, setClock] = useState(localStorage.getItem("clock") || "igt");
  const [feed, setFeed] = useState([]);
  const [connected, setConnected] = useState(false);

  const refresh = useCallback(async (c) => {
    try { setView(await getJSON(`/api/session?clock=${c || clock}`)); }
    catch (e) { console.error(e); }
  }, [clock]);

  useEffect(() => { refresh(); }, [clock]);

  useEffect(() => {
    let ws, closed = false;
    function connect() {
      ws = new WebSocket(`ws://${location.host}/ws/events`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false);
        if (!closed) setTimeout(connect, 2000); };
      ws.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        setFeed((f) => [ev, ...f].slice(0, 200));
        if (REFRESH_ON.has(ev.type)) refresh();
      };
    }
    connect();
    return () => { closed = true; ws && ws.close(); };
  }, [refresh]);

  const pickClock = (c) => { localStorage.setItem("clock", c); setClock(c); };
  return { view, clock, pickClock, feed, connected, refresh };
}
