// src/sm64_events/ui/store.js — session state + live WS subscription
import { useEffect, useRef, useState, useCallback } from "preact/hooks";
import { getJSON, send } from "./api.js";
import { getRankIconStyle, setRankIconStyle } from "./components/rankicon.js";

const REFRESH_ON = new Set(["attempt_completed", "attempts_invalidated",
  "pb_saved", "pb_undone", "session_started", "target_changed",
  "star_collected", "strat_set", "rank_standards_changed",
  "rank_mode_changed", "icons_changed", "marelo_changed", "route_selected"]);
const RUN_REFRESH_ON = new Set(["run_started", "run_progress",
  "run_finished", "run_aborted", "game_reset"]);

export function useTracker() {
  const [view, setView] = useState(null);
  const [clock, setClock] = useState(localStorage.getItem("clock") || "igt");
  const [scope, setScope] = useState(localStorage.getItem("scope") || "session");
  // Star-selector art: "course" (default) = each star's split-icon
  // (ui/assets/star_icons/), "classic" = the generic gold star. Client
  // display preference, so localStorage like clock/scope — not server
  // state. Per-entity icon OVERRIDES are server state (view's
  // icon_overrides) and win in either mode.
  const [starIcons, setStarIcons] = useState(
    localStorage.getItem("sm64.starIcons") || "course");
  // Rank-icon STYLE: which registered style (rankicon.js::ICON_STYLES) draws
  // a rank -- "hat" (Mario caps, default) or "medal" (colour disc), and any
  // future style added to that registry. Client display preference like
  // starIcons above: localStorage `sm64.rankIcons`, never server state.
  // rankicon.js owns the actual persisted value + the live update every
  // mounted RankIcon subscribes to directly (most rank-icon call sites have
  // no `t` in scope to read this off) -- this state exists only so
  // header.js's settings control has the same t.<pref>/pickX shape every
  // other display preference here already has.
  const [rankIcons, setRankIconsState] = useState(getRankIconStyle());
  // Dust-trick visibility: the rollout/jump counts on attempt rows plus the
  // dust stats in the stat menu and chip row. Default OFF while detection is
  // being tuned (2026-07-24). Client display preference like starIcons.
  const [showDust, setShowDust] = useState(
    localStorage.getItem("sm64.showDust") === "1");
  // Course portrait manifest (stem -> filename) from GET /api/icons/courses.
  // Fetched ONCE: the set only changes with the install. Empty object until it
  // lands, which the icon chain treats as "no portrait" and falls back to star
  // art — so a slow fetch degrades to the same art the four painting-less
  // courses use, never to a broken image.
  const [courseIcons, setCourseIcons] = useState({});
  useEffect(() => {
    getJSON("/api/icons/courses")
      .then((payload) => setCourseIcons(payload.courses || {}))
      .catch(() => {});      // no portraits is a survivable state
  }, []);
  // Segment definitions + the builder vocabulary, for the pickers. Segments
  // are refetched when the server says they changed (a new definition should
  // appear in the target picker without a reload); vocab is static per
  // install, so it is fetched once.
  const [segments, setSegments] = useState([]);
  const [vocab, setVocab] = useState({});
  const loadSegments = () => getJSON("/api/segments")
    .then(setSegments).catch(() => {});   // degraded mode: no segments listed
  useEffect(() => {
    loadSegments();
    getJSON("/api/segments/vocab").then(setVocab).catch(() => {});
  }, []);
  const [feed, setFeed] = useState([]);
  const [connected, setConnected] = useState(false);
  // armedOrder: live armed membership (drives the honest "armed" chip) —
  // reconciled from each view fetch (membership authoritative, order
  // best-effort). armedSegs (Set) is derived from it.
  const [armedOrder, setArmedOrder] = useState([]);
  // armedNames: segment_id -> display name, fed by segment_armed payloads and
  // every view fetch. Lets tab-independent surfaces (the header's "running"
  // chip) name an armed segment even before the next view lands — live report
  // 2026-07-23: a segment armed and timed a full run with zero indication on
  // the header/banner/Segments tab, so the user assumed nothing was armed.
  const [armedNames, setArmedNames] = useState({});
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

  // marelo: the ACTIVE-scope MARELO figure (no ?scope= -> ranks_api
  // _active_scope, the focus route) -- the header bar's cap AND the
  // rank-up overlay's celebration both read this ONE fetch. Held here
  // rather than locally in header.js so app.js can mount the overlay at
  // root (browser<->GUI parity, rule 10) off the same object the header
  // renders, instead of a second independent poll that could disagree
  // about whether a celebration is pending.
  const [marelo, setMarelo] = useState(null);
  // mareloRev: bumped alongside every REFRESH_ON event. The Rank tab fetches
  // its OWN scope-scoped /api/marelo + /api/marelo/history + breakdown and
  // has no other way to notice a finished run while it stays mounted --
  // without this it goes stale while open during play (spec 2026-07-24
  // Step 2b). A counter, not a boolean: RankPage puts it straight in an
  // effect's dependency list, and every REFRESH_ON tick must be a distinct
  // dependency value even if two land back-to-back.
  const [mareloRev, setMareloRev] = useState(0);
  const refreshMarelo = useCallback(async () => {
    try { setMarelo(await getJSON("/api/marelo")); } catch (e) { console.error(e); }
  }, []);
  useEffect(() => { refreshMarelo(); }, [refreshMarelo]);
  // clearMareloCelebration: local-only clear so the overlay disappears the
  // instant it acks, without waiting on the next REFRESH_ON fetch to bring
  // back a marelo object whose celebration the server has already retired.
  const clearMareloCelebration = useCallback(() => {
    setMarelo((prev) => prev ? { ...prev, celebration: null } : prev);
  }, []);
  // clearEntityCelebration: the sibling for the entity_celebrations LIST --
  // drops just the one entity that was actually shown+acked (task F3), the
  // same instant-local-clear reasoning as clearMareloCelebration above.
  const clearEntityCelebration = useCallback((entityKey) => {
    setMarelo((prev) => prev ? { ...prev,
      entity_celebrations: prev.entity_celebrations.filter((c) => c.entity !== entityKey) } : prev);
  }, []);

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
      setArmedNames((prev) => {
        const next = { ...prev };
        for (const s of (v && v.segments) || []) next[s.segment_id] = s.name;
        return next;
      });
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
        }
        everConnected.current = true;
        setConnected(true);
        refresh();   // on EVERY open, not just reconnects: reconnects missed
                     // events (the view is the authoritative state), and the
                     // FIRST connect can follow a failed mount-time fetch
                     // (server briefly db-less during an update/restart
                     // handoff — live incident 2026-07-23: view stayed null
                     // forever under a "live" header).
      };
      ws.onclose = () => { setConnected(false);
        if (!closed) setTimeout(connect, 2000); };
      ws.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        setFeed((f) => [ev, ...f].slice(0, 200));
        if (REFRESH_ON.has(ev.type)) {
          refresh();
          refreshMarelo();
          setMareloRev((prevRev) => prevRev + 1);
        }
        if (RUN_REFRESH_ON.has(ev.type)) refreshRun();
        if (ev.type === "segment_armed") {
          const id = ev.payload.segment_id;
          setArmedNames((prev) => ({ ...prev, [id]: ev.payload.name }));
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
        } else if (ev.type === "segments_changed"
                   || ev.type === "origins_changed") {
          // A new or edited definition must show up in the target picker
          // without a reload — the picker reads t.segments.
          loadSegments();
        } else if (ev.type === "stage_changed") {
          setStage(ev.payload);
        }
      };
    }
    connect();
    return () => { closed = true; ws && ws.close(); };
  }, [refresh]);   // refresh is now stable -> this effect runs exactly once

  // Fallback of last resort: the page must never sit on "loading…" while
  // the server is reachable. If the view has NEVER loaded (mount fetch and
  // ws-open refresh both failed — e.g. /api/session 503 while the server
  // waits out a lost instance-lock race), keep retrying until the first
  // view lands; after that, WS-driven refreshes own freshness.
  const viewLoaded = view !== null;
  useEffect(() => {
    if (viewLoaded) return;
    const retry = setInterval(refresh, 3000);
    return () => clearInterval(retry);
  }, [viewLoaded, refresh]);

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
    try {
      const res = await send("POST", "/api/update/apply");
      if (!res || (res.state !== "downloading" && res.state !== "installing")) {
        // begin_apply refused WITHOUT starting the worker (re-check failed,
        // folder not writable): the service stays "idle", so the status poll
        // would render the button-less "Installing…" branch forever. Unstick
        // the popup and surface the reason as a header toast.
        setUpdateApplying(false);
        setUpdateMsg("Update could not start" +
                     (res && res.error ? `: ${res.error}` : ""));
      }
    } catch (e) {
      setUpdateApplying(false);
      setUpdateMsg("Update could not start");
    }
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
  const pickStarIcons = (mode) => {
    localStorage.setItem("sm64.starIcons", mode); setStarIcons(mode); };
  const pickRankIcons = (style) => {
    setRankIconStyle(style); setRankIconsState(style); };
  const pickShowDust = (on) => {
    localStorage.setItem("sm64.showDust", on ? "1" : "0"); setShowDust(on); };
  const armedSegs = new Set(armedOrder);
  return { view, clock, pickClock, scope, pickScope, feed, connected,
           starIcons, pickStarIcons, rankIcons, pickRankIcons,
           showDust, pickShowDust, courseIcons,
           segments, vocab, loadSegments,
           refresh, paused: pauseState.paused,
           pauseReason: pauseState.reason, togglePause,
           armedSegs, armedOrder, armedNames, lastPinnedSeg, stage,
           run, refreshRun,
           marelo, mareloRev, clearMareloCelebration, clearEntityCelebration,
           update, updateForced, setUpdateForced, updateApplying,
           setUpdateApplying, updateMsg, checkUpdates, applyUpdate, skipUpdate };
}
