// src/sm64_events/ui/components/ghostswitch.js
//
// The Ghost Overlay control on the practice card. Griffin's own words for
// what this is: "we would just need to add an iOS style switch button that
// says 'Ghost Overlay' -- it takes the template we have for the current star
// / segment we have activated ... overlays it directly on top of the PJ64
// window", and "'Ghost Overlay' is disabled / not selectable if the user
// hasn't set up a template for the star/segment yet."
//
// It draws ONE app-wide session (interface I5: GET/PUT /api/ghost, broadcast
// ghost_changed) and nothing of its own. Every rule about what that session
// MEANS lives in ../ghostsession.js, import-free and node-driven -- this file
// is markup, fetches and the one write policy below.
//
// THE WRITE POLICY, and why it is two shapes rather than one:
//   * the switch writes on the gesture. A flip is one decision and the
//     server answers with the whole session, so there is nothing to batch.
//   * the slider and the timing buttons write on a TRAILING edge. Dragging a
//     range fires an event per pixel and a held nudge steps about fifteen
//     times a second (ui/holdrepeat.js, his own 0.25s-then-repeat rule), so
//     writing per event would put a burst of PUTs on the wire for one
//     gesture. What is shown updates on the event regardless -- the draft
//     below is optimistic -- so nothing he can see waits for the network.
//     This is deliberately not a debounce of the DISPLAY: a wait he can see
//     is a defect here, whatever it saves.
//
// While a write is in flight, or one is queued, an arriving ghost_changed is
// IGNORED. The broadcast of our own write lands before the write's response
// does, and adopting whatever is on the wire mid-gesture is how a control
// snaps back under the finger (ui/components/stratpicker.js paid for that
// lesson on the strategy select, measured on his own journal).
import { h } from "preact";
import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { holdRepeat } from "../holdrepeat.js";
import { clampNudge, clampOpacity, ghostControls, newestGhostSession,
         nudgeSeconds, nudgeText, opacityText,
         NUDGE_STEP, OPACITY_MAX, OPACITY_MIN } from "../ghostsession.js";

const html = htm.bind(h);

export const GHOST_URL = "/api/ghost";
// Long enough that a drag or a held nudge settles into one write, short
// enough that letting go and looking back at the game is already committed.
export const WRITE_SETTLE_MS = 180;

// An entity key is "star:8:1" / "segment:12"; ids built from it join the
// switch to its own label and its reason.
const idFor = (entityKey, part) =>
  `ghost-${part}-${String(entityKey).replace(/[^a-zA-Z0-9]+/g, "-")}`;

/**
 * The app's one ghost session, plus the two doors to change it.
 *
 * `reachable` is false when the session door is not there at all (a build
 * without it, or a server that has not wired it yet) -- the card then draws
 * NOTHING, which is the honest degrade: a control that cannot reach its own
 * server is worse than no control, and `store.js` already treats a missing
 * door this way for the segment list.
 */
export function useGhostSession(t, active = true) {
  const [session, setSession] = useState(null);
  const [reachable, setReachable] = useState(true);
  const [draft, setDraft] = useState(null);
  const inFlight = useRef(0);
  const timer = useRef(null);
  const queued = useRef(null);

  // `active` is why this hook exists on every card and reads on ONE. The
  // practice log holds up to nine cards; a session read per card would be
  // nine requests for one app-wide answer. Gating the EFFECT rather than the
  // mount is what lets the host stay a plain unconditional element -- see
  // GhostSwitch below.
  useEffect(() => {
    if (!active) return undefined;
    let dropped = false;
    getJSON(GHOST_URL)
      .then((answer) => { if (!dropped) setSession(answer); })
      .catch(() => { if (!dropped) setReachable(false); });
    return () => { dropped = true; };
  }, [active]);

  const put = useCallback(async (body) => {
    inFlight.current += 1;
    try {
      setSession(await send("PUT", GHOST_URL, body));
    } catch (error) {
      // The server writes the sentence (api.js carries its `detail`
      // through); raising it is what every other write on this page does.
      if (t.setNotice) t.setNotice(error.message);
    } finally {
      inFlight.current -= 1;
      if (inFlight.current === 0 && timer.current === null) setDraft(null);
    }
  }, [t]);

  // Show it now, send it once the gesture stops.
  //
  // `patch` may be a FUNCTION of whatever is already queued, and that is not
  // a convenience -- it is the fix for a bug this rig caught. A step button
  // computes "one more than the current value", and the current value it can
  // see is the one captured when its handler was built. Nothing re-renders
  // inside a single tick, and `holdRepeat` keeps calling the SAME step
  // function for the whole press, so five quick presses (or a one-second
  // hold, which is fifteen) all computed +1 from the same starting point and
  // the ghost moved a single frame. Reading the QUEUED value instead makes a
  // burst accumulate; once the window flushes, `queued` is empty again and
  // the caller's own value is the base.
  const queue = useCallback((patch) => {
    const body = typeof patch === "function"
      ? patch(queued.current || {}) : patch;
    setDraft((prev) => ({ ...(prev || {}), ...body }));
    queued.current = { ...(queued.current || {}), ...body };
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      const settled = queued.current;
      queued.current = null;
      if (settled) put(settled);
    }, WRITE_SETTLE_MS);
  }, [put]);

  // Show it now, send it now.
  const write = useCallback((patch) => {
    setDraft((prev) => ({ ...(prev || {}), ...patch }));
    put(patch);
  }, [put]);

  useEffect(() => () => {
    if (timer.current !== null) clearTimeout(timer.current);
  }, []);

  // The broadcast. `newest` is the payload object the socket handler stored,
  // so its identity changes only when a genuinely new ghost_changed lands.
  const newest = newestGhostSession(t.feed);
  useEffect(() => {
    if (!newest || !active) return;
    if (inFlight.current > 0 || timer.current !== null) return;
    setSession(newest);
    setDraft(null);
  }, [newest, active]);

  return { session, reachable, draft, write, queue };
}

function GhostSwitchButton({ on, canToggle, onToggle, labelId, reasonId }) {
  return html`<button type="button" role="switch" class="ghost-switch"
      aria-checked=${on ? "true" : "false"} aria-labelledby=${labelId}
      aria-describedby=${reasonId || undefined}
      disabled=${!canToggle} onclick=${onToggle}>
    <span class="ghost-switch-track" aria-hidden="true">
      <span class="ghost-switch-knob"></span>
    </span>
  </button>`;
}

/**
 * The two things worth reaching for while the ghost is up, and nothing else:
 * how solid it is drawn over the game, and where it sits against your own
 * run. The whole block is absent while the overlay is off -- there would be
 * nothing for either to act on, and the acceptance rule is to omit an action
 * that does not apply rather than to disable it.
 */
function GhostTuning({ opacity, nudge, queue }) {
  // Against whatever is already queued, never against this render's value --
  // see `queue` above for the press-and-hold this exists for.
  const stepNudge = (delta) => queue((pending) => ({
    nudge_frames: clampNudge(
      (pending.nudge_frames == null ? nudge : pending.nudge_frames) + delta),
  }));
  return html`<div class="ghost-tune">
    <label class="ghost-field">
      <span class="ghost-field-name">Visibility</span>
      <input class="ghost-opacity" type="range" min=${OPACITY_MIN}
        max=${OPACITY_MAX} step="0.05" value=${opacity}
        title="How solid the ghost is drawn over the game"
        oninput=${(event) => queue({ opacity: Number(event.target.value) })} />
      <span class="ghost-field-value">${opacityText(opacity)}</span>
    </label>
    <div class="ghost-field ghost-timing" role="group" aria-label="Ghost timing">
      <span class="ghost-field-name">Timing</span>
      <button type="button" class="ghost-step"
          title="Show the ghost one game frame further ahead of you"
          ...${holdRepeat(() => stepNudge(NUDGE_STEP))}>Earlier</button>
      <span class="ghost-field-value ghost-nudge-value"
          title=${`${nudgeSeconds(nudge).toFixed(2)} seconds`}>
        ${nudgeText(nudge)}
      </span>
      <button type="button" class="ghost-step"
          title="Show the ghost one game frame further behind you"
          ...${holdRepeat(() => stepNudge(-NUDGE_STEP))}>Later</button>
      ${nudge !== 0 && html`<button type="button" class="ghost-reset"
          title="Put the ghost back in step with your own run"
          onclick=${() => queue({ nudge_frames: 0 })}>Reset</button>`}
    </div>
  </div>`;
}

/**
 * The row itself. `entityKey` is the hosting card's own key -- the control
 * refuses to speak for a session that names a different entity (see
 * ghostsession.js), so a target change cannot flash the previous star's
 * refusal under this one's name.
 *
 * `active` is the ACTIVE-CARD RULE, and it lives here rather than at the
 * mount on purpose. `ui/components/practicelog.js` is being rebuilt on
 * another branch, so this card's whole cost there is ONE unconditional
 * element on an existing line; the condition that would otherwise sit beside
 * it is this prop, and it is what both the fetch above and the early return
 * below read. Nothing renders and nothing is requested on the other cards.
 */
export function GhostSwitch({ t, entityKey, active = true }) {
  const { session, reachable, draft, write, queue } = useGhostSession(t, active);
  if (!active || !reachable) return null;
  const controls = ghostControls(session, entityKey);
  const held = draft || {};
  const on = held.enabled == null
    ? controls.enabled : Boolean(held.enabled) && controls.available;
  const opacity = held.opacity == null
    ? controls.opacity : clampOpacity(held.opacity);
  const nudge = held.nudge_frames == null
    ? controls.nudge : clampNudge(held.nudge_frames);
  const labelId = idFor(entityKey, "label");
  const reasonId = controls.reasonText ? idFor(entityKey, "why") : null;

  return html`<div class="ghost-row ${on ? "is-on" : ""}">
    <div class="ghost-row-head">
      <${GhostSwitchButton} on=${on} canToggle=${controls.canToggle}
        labelId=${labelId} reasonId=${reasonId}
        onToggle=${() => write({ enabled: !on })} />
      <span class="ghost-label" id=${labelId}>Ghost Overlay</span>
      ${controls.reasonText && html`<span class="ghost-why" id=${reasonId}>
        ${controls.reasonText}
      </span>`}
    </div>
    ${on && html`<${GhostTuning} opacity=${opacity} nudge=${nudge}
      queue=${queue} />`}
  </div>`;
}
