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
  const [armedSegs, setArmedSegs] = useState(new Set());
  // server-owned pause truth: {paused, reason: "manual"|"afk"|null}.
  // Polled (5 s) because "afk" flips server-side without any UI action;
  // the POST response updates it instantly on manual toggles.
  const [pauseState, setPauseState] = useState({ paused: false, reason: null });
  const reasonRef = useRef(null);
  useEffect(() => { reasonRef.current = pauseState.reason; }, [pauseState]);
  useEffect(() => {
    let alive = true;
    const poll = () => getJSON("/api/pause")
      .then((r) => alive && setPauseState(r)).catch(() => {});
    poll();
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  // The button drives only the MANUAL layer: pausing while afk escalates
  // to manual (movement no longer resumes); resume exists only for manual.
  const togglePause = useCallback(async () => {
    try {
      const r = await send("POST", "/api/pause",
                           { paused: reasonRef.current !== "manual" });
      setPauseState(r);
    } catch (e) { console.error(e); }
  }, []);

  // clockRef / scopeRef keep refresh's identity stable so the WS effect never restarts
  const clockRef = useRef(clock);
  const scopeRef = useRef(scope);
  const everConnected = useRef(false);
  useEffect(() => { clockRef.current = clock; }, [clock]);
  useEffect(() => { scopeRef.current = scope; }, [scope]);

  const refresh = useCallback(async () => {
    try {
      const v = await getJSON(`/api/session?clock=${clockRef.current}&scope=${scopeRef.current}`);
      setView(v);
      // armedSegs: live via WS notices, reconciled from every view fetch —
      // instant AND cannot stay stale across reconnects.
      setArmedSegs(new Set(((v && v.segments) || [])
        .filter((s) => s.armed).map((s) => s.segment_id)));
    }
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
          refresh();   // events were missed during the outage — the view is
                       // the authoritative state (armed flags, attempts, target)
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
        if (ev.type === "segment_armed") {
          setArmedSegs((s) => new Set(s).add(ev.payload.segment_id));
        } else if (ev.type === "segment_disarmed") {
          setArmedSegs((s) => { const n = new Set(s);
            n.delete(ev.payload.segment_id); return n; });
        }
      };
    }
    connect();
    return () => { closed = true; ws && ws.close(); };
  }, [refresh]);   // refresh is now stable -> this effect runs exactly once

  const pickClock = (c) => { localStorage.setItem("clock", c); setClock(c); };
  const pickScope = (s) => { localStorage.setItem("scope", s); setScope(s); };
  return { view, clock, pickClock, scope, pickScope, feed, connected,
           refresh, paused: pauseState.paused,
           pauseReason: pauseState.reason, togglePause, armedSegs };
}
