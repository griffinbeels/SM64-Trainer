// src/sm64_events/ui/store.js — session state + live WS subscription
import { useEffect, useRef, useState, useCallback } from "preact/hooks";
import { getJSON, send } from "./api.js";

const REFRESH_ON = new Set(["attempt_completed", "attempts_invalidated",
  "pb_saved", "session_started", "target_changed", "star_collected",
  "strat_set"]);

export function useTracker() {
  const [view, setView] = useState(null);
  const [clock, setClock] = useState(localStorage.getItem("clock") || "igt");
  const [scope, setScope] = useState(localStorage.getItem("scope") || "session");
  const [feed, setFeed] = useState([]);
  const [connected, setConnected] = useState(false);
  const [paused, setPaused] = useState(false);

  // server is the truth for pause (survives page reloads / other tabs)
  const pausedRef = useRef(false);
  useEffect(() => { pausedRef.current = paused; }, [paused]);
  useEffect(() => {
    getJSON("/api/pause").then((r) => setPaused(r.paused)).catch(() => {});
  }, []);
  const togglePause = useCallback(async () => {
    try {
      const r = await send("POST", "/api/pause",
                           { paused: !pausedRef.current });
      setPaused(r.paused);
    } catch (e) { console.error(e); }
  }, []);

  // clockRef / scopeRef keep refresh's identity stable so the WS effect never restarts
  const clockRef = useRef(clock);
  const scopeRef = useRef(scope);
  const everConnected = useRef(false);
  useEffect(() => { clockRef.current = clock; }, [clock]);
  useEffect(() => { scopeRef.current = scope; }, [scope]);

  const refresh = useCallback(async () => {
    try { setView(await getJSON(`/api/session?clock=${clockRef.current}&scope=${scopeRef.current}`)); }
    catch (e) { console.error(e); }
  }, []);

  useEffect(() => { refresh(); }, [clock, scope, refresh]);

  useEffect(() => {
    let ws, closed = false;
    function connect() {
      ws = new WebSocket(`ws://${location.host}/ws/events`);
      ws.onopen = () => {
        if (everConnected.current) {
          setFeed((f) => [{ type: "ws_reconnected", seq: "", frame: "",
                            payload: {} }, ...f].slice(0, 200));
        }
        everConnected.current = true;
        setConnected(true);
      };
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
  }, [refresh]);   // refresh is now stable -> this effect runs exactly once

  const pickClock = (c) => { localStorage.setItem("clock", c); setClock(c); };
  const pickScope = (s) => { localStorage.setItem("scope", s); setScope(s); };
  return { view, clock, pickClock, scope, pickScope, feed, connected,
           refresh, paused, togglePause };
}
