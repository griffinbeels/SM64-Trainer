// src/sm64_events/ui/store.js — session state + live WS subscription
import { useEffect, useRef, useState, useCallback } from "preact/hooks";
import { getJSON, send } from "./api.js";
import { coalesce } from "./coalesce.js";
import { noteEvent, noteFetchDone, noteFetchStart } from "./latency.js";
import { getRankIconStyle, setRankIconStyle } from "./components/rankicon.js";

// segment_progress: an armed segment's step cursor moved. It is the ONLY
// signal that reaches the browser for it -- a cursor move journals nothing of
// its own, and the position events that cause it (area_changed/level_changed)
// are deliberately not in this set. Without it the step track sits on a step
// the player passed until some unrelated event happens to force a fetch: 77
// seconds, on the live report that produced this (2026-08-02, WF -> SSL).
const REFRESH_ON = new Set(["attempt_completed", "attempts_invalidated",
  "pb_saved", "pb_undone", "session_started", "target_changed",
  "star_collected", "strat_set", "rank_standards_changed",
  "rank_mode_changed", "icons_changed", "marelo_changed", "route_selected",
  "segment_progress",
]);
const RUN_REFRESH_ON = new Set(["run_started", "run_progress",
  "run_finished", "run_aborted", "game_reset"]);
// Sentinel for "this client has no pending route pick of its own" -- distinct
// from `null`, which is the real value for a deliberate "Overall" pick. See
// pickRoute/flushRouteIntent below (live report 2026-07-28: rapid route
// switching got permanently stuck).
const NO_ROUTE_INTENT = Symbol("no-pending-route-intent");

// Returned by `pickRoute` when the pick would abandon an in-flight run. A
// distinct value rather than `false`, so a caller cannot mistake "blocked" for
// "wrote nothing because it was already that route".
export const RUN_ACTIVE = "run-active";

// Would this scope change abandon a run the player is in the middle of?
// Import-free and total, so tests/test_ui_run_scope.py can drive it directly.
// Re-picking the route you are ALREADY running is not a change and must not
// warn — a strategy edit or a stray re-render must never look like an
// abandonment.
export function runBlocksScopeChange(run, currentRouteId, nextRouteId) {
  if (!run || !run.active) return false;
  return nextRouteId !== currentRouteId;
}

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
  // Ending a run lives HERE rather than in runview.js alone, because the
  // scope-change confirmation below has to be able to do it too and a second
  // spelling of "abandon the run" is how the two come apart.
  const endRun = useCallback(async () => {
    try { await send("POST", "/api/run/end"); } catch (e) { /* report upstream */ }
    refreshRun();
  }, [refreshRun]);
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
  // There is no entity sibling for the above: per-ENTITY rank-ups are no
  // longer a payload to hold and dismiss. They are performed live by the
  // rank banner itself climbing (ui/rankclimb.js, task 0012, 2026-07-26), so
  // there is nothing for a client to ack and nothing to clear.

  // The practice plan: WHICH route the user is practising. Store-owned since
  // 2026-07-28 because it now has two surfaces -- the header's route rank
  // card (which is also the picker) and the Practice tab's route focus -- and
  // a component's useState cannot be reached by a sibling. localStorage is an
  // optimistic mirror; the journaled route_selected is the source of truth
  // (spec 2026-07-23 section 5), which is what the reconcile effect below
  // keeps true across a restart.
  const [routes, setRoutes] = useState([]);
  const [activeRouteId, setActiveRouteId] = useState(() => {
    const s = localStorage.getItem("sm64.activeRoute");
    return s ? Number(s) : null;
  });
  // The practice plan and the Rank tab's scope picker are ONE list, from
  // ONE endpoint (user, 2026-07-27: "These should be identical lists and
  // should be the exact same set of options that trigger the exact same
  // things"). `/api/marelo/scopes` is that list -- the same labels, in the
  // same order, with "Overall" as the first entry rather than a separate
  // "All practice" wording for the same thing. Course scopes are dropped
  // here and only here: a course is a rating you can BROWSE, not a plan
  // you can practise, since there is no route for the focus to follow.
  useEffect(() => {
    getJSON("/api/marelo/scopes")
      .then((body) => setRoutes((body.scopes || [])
        .filter((scope) => scope.kind === "route")
        .map((scope) => ({ id: Number(scope.id.slice("route:".length)),
                           name: scope.label }))))
      .catch(() => {});
  }, []);
  const setRoute = (id) => {
    if (id == null) localStorage.removeItem("sm64.activeRoute");
    else localStorage.setItem("sm64.activeRoute", String(id));
    setActiveRouteId(id);
  };
  // pendingRouteIntent / routeWriteInFlight: THIS client's own unconfirmed
  // pick, serialised through flushRouteIntent so at most ONE
  // /api/route/select write is ever on the wire from this client at a time
  // (live report 2026-07-28: "if I go back and forth and change routes fast
  // enough… it gets stuck on one of the routes").
  //
  // Root cause, found by reproduction (three connected clients, matching
  // browser-tab + desktop-GUI parity, rule 10): the active route is ONE
  // practice-wide setting shared by every connected client, but the OLD code
  // here unconditionally re-POSTed THIS client's remembered `activeRouteId`
  // whenever the server disagreed — on every client, with no way to tell "the
  // server drifted, fix it" from "someone else legitimately just changed it a
  // moment ago". Two clients holding different opinions then fight forever:
  // each one's own corrective POST is itself a disagreement the OTHER
  // corrects right back, broadcasting without bound (measured: session-view
  // GET latency climbing past 400ms as the storm grew, and it never settles
  // on its own). That is also why the OLD reconcile effect could not repair
  // it — the effect WAS the fight.
  //
  // The fix: only a client with a pending intent of its own may ever WRITE.
  // Every other disagreement is ADOPTED (see the reconcile effect below),
  // never re-asserted — so a passive client (or this one, once its own pick
  // has been sent) simply follows the shared setting instead of contesting
  // it, and the loop has nothing left to feed on.
  const pendingRouteIntent = useRef(NO_ROUTE_INTENT);
  const routeWriteInFlight = useRef(false);
  const flushRouteIntent = () => {
    if (routeWriteInFlight.current
        || pendingRouteIntent.current === NO_ROUTE_INTENT) return;
    const id = pendingRouteIntent.current;
    pendingRouteIntent.current = NO_ROUTE_INTENT;
    routeWriteInFlight.current = true;
    // Tell the SERVER too (spec 2026-07-23 §5: localStorage is an optimistic
    // mirror, the journaled route_selected is the source of truth). Without
    // this the active route was never journaled, so every seeded castle-
    // movement segment — all 55 carry the in_active_route guard — could only
    // ever arm as a standalone target, i.e. the route corpus was inert. It
    // also feeds active_route.star_keys, which is what lets the selector show
    // only the route's stars.
    send("POST", "/api/route/select", { route_id: id })
      .then(() => refresh())      // pull the new active_route.star_keys
      .catch(() => {
        // Selection still works locally if the write fails -- but retry on
        // the next flush unless a newer pick has since superseded this one.
        if (pendingRouteIntent.current === NO_ROUTE_INTENT) {
          pendingRouteIntent.current = id;
        }
      })
      .finally(() => {
        routeWriteInFlight.current = false;
        flushRouteIntent();   // a newer pick may have queued while this ran
      });
  };
  // The route IS the rank scope (the header's card is both controls at once —
   // `.claude/rules/ui-ranks.md`), so changing it mid-run would silently
  // re-rate a run against a plan it is not following. His ruling, 2026-08-03:
  // *"we have to stop the run before changing ranking scopes... You're allowed
  // to change it, just that it will also stop their active run. The dialogue
  // should warn them."*
  //
  // Returns the sentinel `RUN_ACTIVE` instead of asking anything: a store must
  // not own a dialog, and a `confirm()` here would be unstyleable, untestable
  // and would block the event loop. The CALLER shows the warning and calls
  // again with `{confirmed: true}` — which is also how `runview.js` arms a run
  // without arguing with itself, since starting a run IS the confirmation.
  const pickRoute = (id, { confirmed = false } = {}) => {
    if (!confirmed && runBlocksScopeChange(run, activeRouteId, id))
      return RUN_ACTIVE;
    setRoute(id);
    pendingRouteIntent.current = id;
    flushRouteIntent();
    return null;
  };
  // flushRouteIntent's own `.catch` above is exactly why the reconcile effect
  // below still needs to exist. localStorage is an optimistic mirror of a
  // JOURNALED decision, the write can fail silently, and the picker restores
  // from localStorage on mount without ever telling the server again. The two
  // then stay diverged forever, invisibly here and very visibly wherever the
  // server DERIVES something from the active route: the header's route rank
  // card reads "Overall" while the practice plan says "16 Star — LBLJ",
  // because `/api/marelo`'s default scope IS the server's active route (live
  // report 2026-07-27).
  //
  // Keyed on the two IDS, not on the view object: `view` is a fresh identity
  // every fetch, so an object dependency here would re-run on every
  // WebSocket event for as long as the server kept disagreeing.
  const serverRouteId = (view && view.active_route && view.active_route.id) ?? null;
  // Reconcile: ADOPT the server's active route whenever this client has no
  // pending pick of its own in flight -- covers a fresh client (never chosen,
  // localStorage empty), the desktop GUI's first run, AND a client that just
  // learned another connected client changed the route. This effect never
  // itself WRITES; the only write path is flushRouteIntent above, which is
  // what keeps two connected clients from re-correcting each other forever.
  useEffect(() => {
    if (routeWriteInFlight.current
        || pendingRouteIntent.current !== NO_ROUTE_INTENT) return;
    if (serverRouteId === activeRouteId) return;
    setRoute(serverRouteId);
  }, [serverRouteId, activeRouteId]);
  // marelo can go stale the same way session view can, for a structural
  // reason: TrackerService.publish() broadcasts route_selected BEFORE it
  // journals the change (server/broadcaster.py's await precedes
  // tracking/service.py's _track), so a refetch triggered by that broadcast
  // can execute and return before the write it is reacting to has actually
  // landed. Session view self-heals via the reconcile effect above (it
  // compares the fetched value against activeRouteId and retries); marelo has
  // no such check anywhere else, so give it the same one -- if the scope this
  // client actually just scored doesn't match what it currently wants, the
  // fetch was stale and gets replayed.
  useEffect(() => {
    if (!marelo) return;
    const wantedScope = activeRouteId == null ? "overall" : `route:${activeRouteId}`;
    if (marelo.scope_id === wantedScope) return;
    refreshMarelo();
  }, [marelo, activeRouteId, refreshMarelo]);

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

  // Every REFRESH_ON event goes through ONE coalescer (coalesce.js): a grab
  // publishes star_collected and attempt_completed in the same server tick, so
  // firing per event meant two /api/session + two /api/marelo fetches back to
  // back, racing each other home. Built once — refresh, refreshMarelo and
  // setMareloRev are all stable across renders.
  const requestRefresh = useRef(null);
  if (requestRefresh.current === null) {
    requestRefresh.current = coalesce(async () => {
      // Bumped at the START of the run rather than per event: the Rank tab
      // keys its own fetches off this counter, so it should reload alongside
      // this one instead of a round trip behind it.
      setMareloRev((prevRev) => prevRev + 1);
      // Stamped around the WHOLE round, not around refresh() alone: both
      // fetches are awaited together, so what the page waits for is the slower
      // of the two and measuring one of them would understate it (latency.js).
      noteFetchStart(new Date().toISOString());
      await Promise.all([refresh(), refreshMarelo()]);
      noteFetchDone(new Date().toISOString());
    });
  }

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
          // BEFORE the request, so the mark is the moment the news arrived
          // rather than the moment we got round to acting on it — the
          // coalescer's own window is one of the four stages being measured.
          noteEvent(ev.type, ev.seq, ev.frame, new Date().toISOString());
          requestRefresh.current();
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
  // notice: a transient, app-wide "the server said no" line (app.js renders it).
  // Added for the practice-target refusal (2026-07-27) -- a rejected write that
  // rejects into a click handler leaves the button looking dead, and the server
  // already writes a sentence that names the fix.
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!notice) return undefined;
    const id = setTimeout(() => setNotice(""), 6000);
    return () => clearTimeout(id);
  }, [notice]);
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
           run, refreshRun, endRun,
           marelo, mareloRev, clearMareloCelebration,
           routes, activeRouteId, pickRoute,
           update, updateForced, setUpdateForced, updateApplying,
           setUpdateApplying, updateMsg, checkUpdates, applyUpdate, skipUpdate,
           notice, setNotice };
}
