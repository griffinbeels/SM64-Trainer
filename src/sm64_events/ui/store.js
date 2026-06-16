// src/sm64_events/ui/store.js — session state + live WS subscription
import { useEffect, useRef, useState, useCallback } from "preact/hooks";
import { getJSON, send } from "./api.js";

const REFRESH_ON = new Set(["attempt_completed", "attempts_invalidated",
  "pb_saved", "pb_undone", "session_started", "target_changed",
  "star_collected", "strat_set"]);
const RUN_REFRESH_ON = new Set(["run_started", "run_progress",
  "run_finished", "run_aborted", "game_reset"]);

export function useTracker() {
  const [view, setView] = useState(null);
  const [clock, setClock] = useState(localStorage.getItem("clock") || "igt");
  const [scope, setScope] = useState(localStorage.getItem("scope") || "session");
  const [feed, setFeed] = useState([]);
  const [connected, setConnected] = useState(false);
  // armedOrder: live armed membership (drives the honest "armed" chip) —
  // reconciled from each view fetch (membership authoritative, order
  // best-effort). armedSegs (Set) is derived from it.
  const [armedOrder, setArmedOrder] = useState([]);
  // lastPinnedSeg: STICKY pin for the practice page — set on every
  // segment_armed, NEVER cleared on segment_disarmed. An accidental exit
  // disarms (correct timing semantics — re-entry re-arms fresh) but the page
  // stays on the segment being practiced until a DIFFERENT segment arms OR
  // the segment SUCCEEDS (a completed run is done — retired in the WS handler
  // below; a stage-entry completion otherwise lingers as "RECENT").
  const [lastPinnedSeg, setLastPinnedSeg] = useState(null);
  // stage: the main course the player is currently in (or null / in_stage:false).
  // Driven by the broadcast-only stage_changed WS event; intentionally NOT in
  // REFRESH_ON — the view's catalog and last_strat_by_star don't depend on it,
  // so a full refetch would be wasted. Seeded from v.stage for initial load.
  const [stage, setStage] = useState(null);
  const [run, setRun] = useState(null);
  const refreshRun = useCallback(async () => {
    try { setRun(await getJSON("/api/run")); } catch (e) { /* keep last */ }
  }, []);
  useEffect(() => { refreshRun(); }, [refreshRun]);

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
      setStage(v ? v.stage : null);
      // armedOrder: live via WS notices, reconciled from every view fetch —
      // instant AND cannot stay stale across reconnects. Keep the existing
      // order filtered to the view's armed ids, then append any view-armed
      // ids not already present (order unknown for those — arbitrary append).
      const viewArmed = new Set(((v && v.segments) || [])
        .filter((s) => s.armed).map((s) => s.segment_id));
      setArmedOrder((prev) => {
        const kept = prev.filter((id) => viewArmed.has(id));
        const keptSet = new Set(kept);
        const appended = [...viewArmed].filter((id) => !keptSet.has(id));
        return [...kept, ...appended];
      });
      // Sticky pin reconcile: only seed an empty pin, and only when the view
      // is unambiguous (exactly one armed segment). Never overwrite — the WS
      // arm events own recency, and a disarm must not clear the pin.
      setLastPinnedSeg((prev) =>
        prev == null && viewArmed.size === 1 ? [...viewArmed][0] : prev);
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
        if (RUN_REFRESH_ON.has(ev.type)) refreshRun();
        if (ev.type === "segment_armed") {
          const id = ev.payload.segment_id;
          setArmedOrder((prev) => prev.includes(id) ? prev : [...prev, id]);
          setLastPinnedSeg(id);   // sticky: only another arm moves the pin
        } else if (ev.type === "segment_disarmed") {
          const id = ev.payload.segment_id;
          setArmedOrder((prev) => prev.filter((x) => x !== id));
          // lastPinnedSeg deliberately NOT cleared — see its declaration
        } else if (ev.type === "attempt_completed"
                   && ev.payload.kind === "segment"
                   && ev.payload.outcome === "success") {
          // A finished segment run retires the sticky pin (the run is DONE —
          // unlike an accidental disarm, which keeps it). If the segment ended
          // by entering a star stage the server leaves NO active target, so
          // without this the segment would linger pinned as "RECENT"; a
          // success that does NOT enter a stage stays pinned via the segment
          // target (activeSeg). Matched to projection.py caveat 12. We key off
          // attempt_completed, NOT target_changed: a stage-entry completion
          // leaves target already-None (it was None mid-run), so no transition
          // fires and target_changed never arrives — see projection.py.
          setLastPinnedSeg((prev) =>
            prev === ev.payload.segment_id ? null : prev);
        } else if (ev.type === "stage_changed") {
          setStage(ev.payload);
        }
      };
    }
    connect();
    return () => { closed = true; ws && ws.close(); };
  }, [refresh]);   // refresh is now stable -> this effect runs exactly once

  // --- auto-update (shared so the header "Check for updates" button and the
  // popup agree on one status / one in-flight install) ---
  const [update, setUpdate] = useState(null);              // /api/update/status dict
  const [updateForced, setUpdateForced] = useState(false); // manual check found one -> show despite Skip/Later
  const [updateApplying, setUpdateApplying] = useState(false);
  const [updateMsg, setUpdateMsg] = useState("");          // transient header toast
  const fetchUpdate = useCallback(async (force) => {
    try {
      const st = await getJSON("/api/update/status" + (force ? "?force=1" : ""));
      setUpdate(st);
      return st;
    } catch (e) { return null; }
  }, []);
  useEffect(() => { fetchUpdate(false); }, [fetchUpdate]);  // passive check once on load
  const checkUpdates = useCallback(async () => {           // the header button
    setUpdateMsg("Checking…");
    const st = await fetchUpdate(true);                    // force=1 bypasses the server cache
    if (!st) setUpdateMsg("Check failed");
    else if (st.update_available) { setUpdateForced(true); setUpdateMsg(""); }
    else if (!st.frozen) setUpdateMsg(`v${st.current} — updates run in the packaged app`);
    else setUpdateMsg(`Up to date · v${st.current}`);
  }, [fetchUpdate]);
  useEffect(() => {   // auto-clear the toast, but keep "Checking…" until the result lands
    if (!updateMsg || updateMsg === "Checking…") return;
    const id = setTimeout(() => setUpdateMsg(""), 5000);
    return () => clearTimeout(id);
  }, [updateMsg]);
  const applyUpdate = useCallback(async () => {
    setUpdateApplying(true);
    try { await send("POST", "/api/update/apply"); } catch (e) { /* status poll shows error */ }
  }, []);
  useEffect(() => {   // poll progress while installing; the WS drop on restart ends the session
    if (!updateApplying) return;
    const id = setInterval(() => fetchUpdate(false), 700);
    return () => clearInterval(id);
  }, [updateApplying, fetchUpdate]);
  const skipUpdate = useCallback(async (version) => {
    try { await send("POST", "/api/update/skip", { version }); } catch (e) { /* ignore */ }
    setUpdate((u) => (u ? { ...u, skipped: version } : u));
    setUpdateForced(false);
  }, []);

  const pickClock = (c) => { localStorage.setItem("clock", c); setClock(c); };
  const pickScope = (s) => { localStorage.setItem("scope", s); setScope(s); };
  const armedSegs = new Set(armedOrder);
  return { view, clock, pickClock, scope, pickScope, feed, connected,
           refresh, paused: pauseState.paused,
           pauseReason: pauseState.reason, togglePause,
           armedSegs, armedOrder, lastPinnedSeg, stage,
           run, refreshRun,
           update, updateForced, setUpdateForced, updateApplying,
           setUpdateApplying, updateMsg, checkUpdates, applyUpdate, skipUpdate };
}
