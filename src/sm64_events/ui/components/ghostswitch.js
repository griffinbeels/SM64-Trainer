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
//   * the switches write on the gesture -- the overlay's own and the colour
//     treatment's alike. A flip is one decision and the server answers with
//     the whole session, so there is nothing to batch.
//   * the sliders and the timing buttons write on a TRAILING edge. Dragging a
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
import { GhostProximity } from "./ghostproximity.js";
import { GhostLibraryButton } from "./ghostlibrary.js";
import { clampNudge, clampOpacity, ghostControls, ghostPathTuning, ghostRecordingOptions,
         ghostOption, hueText, newestGhostSession, nudgeLimit, SWITCH_LABELS,
         nudgeSeconds, nudgeText, opacityText, pathTuningText, shownTint,
         NUDGE_STEP, PATH_SOURCE_LABELS, PATH_TUNING_LABELS } from "../ghostsession.js";

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
      return {applied: true};
    } catch (error) {
      // The server writes the sentence (api.js carries its `detail`
      // through); raising it is what every other write on this page does.
      if (t.setNotice) t.setNotice(error.message);
      return {applied: false, error: error.message};
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
    return put(patch);
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

/**
 * The iOS-style switch, at either of the two sizes this row uses. ONE
 * implementation: the colour treatment's own switch is the same control a size
 * down, so the two can never drift into looking like different kinds of thing
 * (`small` adds a class and nothing else). `labelId` names it where a visible
 * label sits beside it; `label` is for the smaller one, whose name is the
 * field it sits in.
 */
function GhostSwitchButton({ on, canToggle, onToggle, labelId, reasonId,
                             label, small = false }) {
  return html`<button type="button" role="switch"
      class=${`ghost-switch${small ? " ghost-switch-sm" : ""}`}
      aria-checked=${on ? "true" : "false"}
      aria-labelledby=${labelId || undefined} aria-label=${label || undefined}
      aria-describedby=${reasonId || undefined}
      disabled=${!canToggle} onclick=${onToggle}>
    <span class="ghost-switch-track" aria-hidden="true">
      <span class="ghost-switch-knob"></span>
    </span>
  </button>`;
}

/**
 * HOW THE GHOST IS COLOURED, so it cannot be read as the live game.
 *
 * His words: *"Maybe we can have a color picker (like on the rank page), and
 * then apply a color filter to the entire picture? e.g. 'Hue Adjustment: Shift
 * the hue angle by +120 degrees'... The user can disable the color shift if
 * they want."*
 *
 * NOT the rank page's picker, and the reason is what the two controls answer.
 * That one (`components/sheetstyle.js`) is `<input type="color">` -- a whole
 * RGB colour, for a sheet cell that is PAINTED that colour. This shifts every
 * colour in a picture around the wheel by one ANGLE, so a hex would throw away
 * two thirds of what he picked: navy and sky blue are the same shift, the
 * swatch would not match anything on his screen, and there would be no way to
 * ask for "no shift" and still have the treatment on. So: the simplest native
 * control that expresses an angle, which is also the affordance already beside
 * it -- a range, wearing the colour wheel as its track so the choice is
 * visible rather than a number to decode.
 *
 * The slider is OMITTED rather than disabled while the shift is off: there is
 * nothing for it to act on, and the acceptance rule is to omit an action that
 * does not apply rather than to grey it out.
 */
/**
 * One Only-Mario on/off setting: a small switch after its name. Unknown (null
 * until the server says) is drawn unknown and cannot be pressed, never off.
 */
function GhostOnOff({ setting, value, draft, write }) {
  const words = SWITCH_LABELS[setting];
  const on = draft == null ? value : Boolean(draft);
  const known = on != null;
  return html`<div class="ghost-field ghost-onoff ${words.className}" role="group" aria-label=${words.group}
      title=${known ? words.label : words.waiting}>
    <span class="ghost-field-name">${words.name}</span>
    <${GhostSwitchButton} on=${known && on} canToggle=${known} small=${true}
      label=${known ? words.label : words.waiting}
      onToggle=${() => write({ [setting]: !(known && on) })} />
  </div>`;
}

/** The stick's presentation depends on Inputs, so it nests beneath that switch. */
/**
 * THE BOX BEHIND EACH NAME. His words, 2026-10-02: *"The nametag should have a
 * black background like this (the text for talking to the whomp king) with
 * adjustable opacity ... Add another option to have a white background like
 * this (the text for the sign)"*. It only means something while names show, so
 * it nests beneath that switch like the stick display beneath Inputs. The
 * colour writes on the pick; the slider on its trailing edge, like Volume.
 */
function GhostNameControls({ row, box, limits, write, queue }) {
  const enabled = row.draft == null ? row.value : Boolean(row.draft);
  const labels = { black: "Black", white: "White" };
  const range = limits.name_background_opacity;
  const solid = box.opacity != null && range != null;
  return html`<div class="ghost-name-controls">
    <${GhostOnOff} setting=${row.setting} value=${row.value} draft=${row.draft} write=${write} />
    ${enabled === true && html`<div class="ghost-field ghost-name-box" role="group"
        aria-label="The box behind each ghost's name">
      <span class="ghost-field-name">Background</span>
      <select aria-label="The colour of the box behind each ghost's name" disabled=${box.background == null}
          value=${box.background || ""} onchange=${(event) => write({ name_background: event.target.value })}>
        ${box.background == null && html`<option value="">Waiting...</option>`}
        ${(limits.name_background?.options || []).map((name) =>
          html`<option key=${name} value=${name}>${labels[name] || name}</option>`)}
      </select>
      <input class="ghost-name-box-range" type="range" min=${range?.min} max=${range?.max} step=${range?.step}
        value=${solid ? box.opacity : null} disabled=${!solid}
        aria-label="How solid the box behind each name is"
        title=${solid ? "How solid the box behind each name is; 0% draws no box"
                      : "Waiting for how solid the box behind each name is"}
        oninput=${(event) => queue({ name_background_opacity: Number(event.target.value) })} />
      <span class="ghost-field-value">${solid ? `${Math.round(box.opacity * 100)}%` : "--"}</span>
    </div>`}
  </div>`;
}

/**
 * THE GHOST'S PATH. His words, 2026-10-03: *"iRacing style path markers on the
 * ground, indicating the path that mario takes, so you know if you're staying
 * on path or not"*. Its presses and which ghosts it is drawn from only mean
 * something while it shows, so they nest beneath the switch like the box
 * beneath Names; its four numbers are the Tune path disclosure below the row.
 */
function GhostPathControls({ row, buttons, source, limits, write }) {
  const enabled = row.draft == null ? row.value : Boolean(row.draft);
  return html`<div class="ghost-path-controls">
    <${GhostOnOff} setting=${row.setting} value=${row.value} draft=${row.draft} write=${write} />
    ${enabled === true && html`<div class="ghost-path-options">
      ${buttons && html`<${GhostOnOff} setting=${buttons.setting} value=${buttons.value}
        draft=${buttons.draft} write=${write} />`}
      <div class="ghost-field ghost-path-source" role="group" aria-label="Which ghosts the path is drawn from">
        <span class="ghost-field-name">Path from</span>
        <select aria-label="Which ghosts the path is drawn from" disabled=${source == null}
            title="Average of ghosts runs through the middle of their routes where they stay together, and follows the fastest ghost where they split"
            value=${source || ""} onchange=${(event) => write({ path_source: event.target.value })}>
          ${source == null && html`<option value="">Waiting...</option>`}
          ${(limits.path_source?.options || []).map((name) =>
            html`<option key=${name} value=${name}>${PATH_SOURCE_LABELS[name] || name}</option>`)}
        </select>
      </div>
    </div>`}
  </div>`;
}

/**
 * HOW THE PATH IS DRAWN: how far ahead it reaches, how wide it is, how
 * strongly it shows and how far apart its arrows sit. Tucked into a
 * disclosure like Overlap fading, because he tunes it once and then races.
 * Each slider writes on its trailing edge and sends only its own number,
 * laid over whatever another slider has in flight (`queue` reads the
 * pending patch, so two quick drags both land).
 */
function GhostPathTune({ active, switches, tuning, ranges, draft, queue }) {
  const path = switches.find((row) => row.setting === "path_enabled");
  const shown = path && (path.draft == null ? path.value : Boolean(path.draft));
  if (!active || !shown || !tuning || !ranges) return null;
  const set = (name, value) => queue((pending) => ({
    path_tuning: { ...(draft || {}), ...(pending.path_tuning || {}), [name]: value },
  }));
  return html`<details class="ghost-path-tune">
    <summary>Tune path</summary>
    <div class="ghost-path-tune-body">
      ${Object.entries(PATH_TUNING_LABELS).filter(([name]) => ranges[name]).map(([name, words]) => html`
        <label key=${name} class="ghost-field">
          <span class="ghost-field-name">${words.name}</span>
          <input class="ghost-path-range" type="range" min=${ranges[name].min} max=${ranges[name].max}
            step=${ranges[name].step} value=${tuning[name]} disabled=${tuning[name] == null}
            aria-label=${words.label} title=${words.label}
            oninput=${(event) => set(name, Number(event.target.value))} />
          <span class="ghost-field-value">${pathTuningText(name, tuning[name], ranges[name])}</span>
        </label>`)}
    </div>
  </details>`;
}

/**
 * The control for one on/off row: Names draws its box setting beneath itself,
 * and Path its presses and its source (so Path buttons draws nothing here).
 * No Stick/Values picker: every ghost input display takes the mode he chose
 * for his own HUD in Usamune's menu (his ask, 2026-10-02: "keep these in sync").
 */
function GhostSwitchControl({ row, rows, nameBox, pathSource, limits, write, queue }) {
  if (row.setting === "names_enabled") {
    return html`<${GhostNameControls} row=${row} box=${nameBox} limits=${limits} write=${write} queue=${queue} />`;
  }
  if (row.setting === "path_enabled") {
    return html`<${GhostPathControls} row=${row} buttons=${rows.find((other) => other.setting === "path_buttons")}
      source=${pathSource} limits=${limits} write=${write} />`;
  }
  if (row.setting === "path_buttons" && rows.some((other) => other.setting === "path_enabled")) return null;
  return html`<${GhostOnOff} setting=${row.setting} value=${row.value} draft=${row.draft} write=${write} />`;
}

/**
 * UNKNOWN IS NOT A POSITION ON THE SLIDER. Until the server has said how
 * solid the ghost is drawn (and what the slider offers), the control carries
 * no author value, cannot be dragged, and says why -- so the only opacity
 * that ever reaches the server is one HE chose. A thumb parked at some
 * default looks exactly like a value he picked, and one small drag from
 * there writes it back for good.
 */
function GhostVisibility({ opacity, range, queue }) {
  const known = opacity != null && range != null;
  return html`<label class="ghost-field">
    <span class="ghost-field-name">Visibility</span>
    <input class="ghost-opacity" type="range" min=${range?.min}
      max=${range?.max} step=${range?.step} value=${known ? opacity : null}
      disabled=${!known}
      title=${known ? "How solid the ghost is drawn over the game"
                    : "Waiting for how solid the ghost is drawn"}
      oninput=${(event) => queue({ opacity: Number(event.target.value) })} />
    <span class="ghost-field-value">${opacityText(opacity)}</span>
  </label>`;
}

/** HOW LOUD GHOST MARIO IS: his own action sounds, mixed under the game.
 * Unknown is drawn unknown, like the opacity: no thumb parked at a default. */
function GhostVolume({ volume, draft, range, queue }) {
  const value = draft == null ? volume : draft;
  const known = value != null && range != null;
  return html`<label class="ghost-field ghost-volume">
    <span class="ghost-field-name">Volume</span>
    <input class="ghost-volume-range" type="range" min=${range?.min} max=${range?.max} step=${range?.step}
      value=${known ? value : null} disabled=${!known}
      title=${known ? "How loud Ghost Mario's sounds play under the game" : "Waiting for the ghost's volume"}
      oninput=${(event) => queue({ volume: Number(event.target.value) })} />
    <span class="ghost-field-value">${known ? `${Math.round(value * 100)}%` : "--"}</span>
  </label>`;
}

function GhostColour({ tint, range, write, queue }) {
  const known = tint != null;
  return html`<div class="ghost-field ghost-colour" role="group"
      aria-label="Ghost colour">
    <span class="ghost-field-name">Colour</span>
    <${GhostSwitchButton} on=${known && tint.enabled} canToggle=${known}
      small=${true}
      label=${known ? "Colour the ghost so it stands out from the live game"
                    : "Waiting for how the ghost is coloured"}
      onToggle=${() => write({ tint: { enabled: !(known && tint.enabled) } })} />
    ${known && tint.enabled && range && html`
      <input class="ghost-hue" type="range" min=${range.min} max=${range.max}
        step=${range.step} value=${tint.hue}
        title="How far the ghost's colours are shifted around the colour wheel"
        oninput=${(event) =>
          queue({ tint: { hue_degrees: Number(event.target.value) } })} />
      <span class="ghost-field-value">${hueText(tint.hue)}</span>`}
  </div>`;
}

/** WHICH GHOSTS PLAY: one tick per recording, his PB first. A tick writes
 * the whole set at once; each ticked ghost wears the colour dot it is drawn
 * in, so several Marios on screen can be told apart. */
function GhostRecording({ recording, entityKey, target, write }) {
  if (!recording) return null;
  const reasonId = recording.reasonText ? idFor(entityKey, "recording-why") : null;
  return html`<div class="ghost-recording-row">
    <fieldset class="ghost-recordings" aria-describedby=${reasonId || undefined}>
      <legend class="ghost-field-name">Ghosts</legend>
      ${recording.items.map((item) => html`<label key=${item.key}
        class="ghost-pick ${item.disabled ? "is-disabled" : ""}"
        title=${item.reason ? `${item.label} — ${item.reason}` : item.label}>
        <input type="checkbox" class="ghost-pick-box" checked=${item.checked}
          disabled=${item.disabled}
          onchange=${() => write({ ghosts: recording.toggle(item.key), target })} />
        ${item.hue != null && html`<span class="ghost-pick-hue" aria-hidden="true"
          style=${`--ghost-hue: ${item.hue}deg`}></span>`}
        <span class="ghost-pick-name">${item.label}</span>
        ${item.reason && html`<span class="ghost-pick-why">${item.reason}</span>`}
      </label>`)}
      ${recording.folded && html`<span class="ghost-picks-folded"
        title=${recording.folded.reason || undefined}>${recording.folded.label}</span>`}
    </fieldset>
    ${recording.reasonText && html`<span class="ghost-why" id=${reasonId}>
      ${recording.reasonText}
    </span>`}
  </div>`;
}

/**
 * The ghost's visibility, colour, annotations, sound, path and timing.
 * The block is absent while the overlay is off -- there would be
 * nothing for any of them to act on, and the acceptance rule is to omit an
 * action that does not apply rather than to disable it.
 */
function GhostTuning({ opacity, tint, switches, nameBox, pathSource, volume, nudge, limits,
                      write, queue }) {
  // Against whatever is already queued, never against this render's value --
  // see `queue` above for the press-and-hold this exists for.
  const stepNudge = (delta) => queue((pending) => ({
    nudge_frames: clampNudge(
      (pending.nudge_frames == null ? nudge : pending.nudge_frames) + delta, nudgeLimit(limits)),
  }));
  const control = (row) => html`<${GhostSwitchControl} key=${row.setting}
    row=${row} rows=${switches} nameBox=${nameBox} pathSource=${pathSource} limits=${limits}
    write=${write} queue=${queue} />`;
  // Volume sits right after Fade its sound, the sound's other control; the
  // switches listed after that one follow Volume.
  const sound = switches.findIndex((row) => row.setting === "sound_fade") + 1 || switches.length;
  return html`<div class="ghost-tune">
    <${GhostVisibility} opacity=${opacity} range=${limits.opacity} queue=${queue} />
    <${GhostColour} tint=${tint} range=${limits.tint} write=${write} queue=${queue} />
    ${switches.slice(0, sound).map(control)}
      <${GhostVolume} volume=${volume.value} draft=${volume.draft} range=${limits.volume}
        queue=${queue} />
    ${switches.slice(sound).map(control)}
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
 * What the tuning row shows: the server's values with his in-flight changes
 * laid over them. The opacity may be null, and that is a state rather than a
 * gap: nobody has said how solid the ghost is drawn, so the row draws it as
 * unknown and no write can carry it (see GhostVisibility). The tint's two
 * halves arrive by different gestures, so its merge is its own rule.
 */
function heldValues(controls, held) {
  const { limits } = controls;
  return {
    opacity: held.opacity == null ? controls.opacity : clampOpacity(held.opacity, limits.opacity),
    nudge: held.nudge_frames == null ? controls.nudge : clampNudge(held.nudge_frames, nudgeLimit(limits)),
    // Every on/off setting the server lists that this card has words for.
    switches: Object.entries(controls.switches)
      .filter(([setting]) => SWITCH_LABELS[setting])
      .map(([setting, value]) => ({ setting, value, draft: held[setting] })),
    tint: shownTint(controls.tint, held.tint, limits.tint),
    nameBox: {
      background: ghostOption("name_background", held.name_background ?? controls.nameBox.background, limits),
      opacity: held.name_background_opacity == null ? controls.nameBox.opacity
        : clampOpacity(held.name_background_opacity, limits.name_background_opacity),
    },
    pathSource: ghostOption("path_source", held.path_source ?? controls.pathSource, limits),
    pathTuning: held.path_tuning == null ? controls.pathTuning
      : ghostPathTuning(controls.pathTuning, limits, held.path_tuning),
  };
}

/** The strategy being practised; the picker names a run's strategy only when it differs. */
function practisedStrat(session) {
  return session?.target?.strat ?? null;
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
  const { limits } = controls;
  const { opacity, nudge, switches, tint, nameBox, pathSource, pathTuning } = heldValues(controls, held);
  const recording = ghostRecordingOptions(controls.selection, held, practisedStrat(session));
  const labelId = idFor(entityKey, "label");
  const reasonId = controls.reasonText ? idFor(entityKey, "why") : null;

  return html`<div class="ghost-row ${on ? "is-on" : ""}">
    <div class="ghost-row-head">
      <${GhostSwitchButton} on=${on} canToggle=${controls.canToggle}
        labelId=${labelId} reasonId=${reasonId}
        onToggle=${() => write({ enabled: !on })} />
      <span class="ghost-label" id=${labelId}>Ghost Overlay</span>
      <${GhostLibraryButton} t=${t} session=${session} write=${write} />
      ${controls.reasonText && html`<span class="ghost-why" id=${reasonId}>
        ${controls.reasonText}
      </span>`}
    </div>
    <${GhostRecording} recording=${recording} entityKey=${entityKey}
      target=${session?.target} write=${write} />
    ${on && html`<${GhostTuning} opacity=${opacity} tint=${tint}
      switches=${switches} nameBox=${nameBox} pathSource=${pathSource} limits=${limits}
      volume=${{ value: controls.volume, draft: held.volume }}
      nudge=${nudge} write=${write} queue=${queue} />`}
    <${GhostProximity} active=${on} value=${controls.proximity} range=${limits.proximity}
      draft=${held.proximity} write=${write} queue=${queue} />
    <${GhostPathTune} active=${on} switches=${switches} tuning=${pathTuning}
      ranges=${limits.path_tuning} draft=${held.path_tuning} queue=${queue} />
  </div>`;
}
