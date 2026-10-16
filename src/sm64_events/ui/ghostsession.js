// src/sm64_events/ui/ghostsession.js
//
// The rules behind the practice card's Ghost Overlay control. No DOM, no
// fetch, no preact -- only the shared time format -- so node drives every rule
// below directly (tests/test_ui_ghost_session.py) instead of a browser.
//
// The server side is ONE session for the whole app (interface I5): it knows
// whether the overlay is on, how solid it is drawn, how many game frames the
// ghost is nudged by, which target it is following, and whether that target
// has recorded Mario poses to play. The card only ever draws that answer.
//
// THE IDENTITY CHECK IS THE POINT OF THIS MODULE. The session names its own
// target, and the card that hosts the switch is the ACTIVE one -- but those
// two agree only after the server has caught up with a target change. Drawing
// the old target's refusal sentence under the new target's name is exactly
// the "never show a state you are about to correct" rule (the sm64-uiux
// skill): so while they disagree the control says it is still looking,
// rather than borrowing an answer about a different star.
//
// The two sides also spell an entity differently and neither is wrong: a
// practice card's key is "star:8:1" / "segment:12" (ui/entitysection.js),
// while a template -- which is what a ghost hangs off -- is filed under
// ("star", "8-1") / ("segment", "12") (inputs/service.py::entity_key_of).
// `ghostTargetKey` is the one place that translation happens.

// The game's own clock, for turning a nudge in frames into a readable
// duration. 30 fps is this project's primary clock everywhere.
import { fmtIgtShort } from "./format.js";

export const GAME_FPS = 30;

// EVERY RANGE COMES FROM THE SERVER. `GET /api/ghost` publishes `limits`
// (ghost/settings.py): what each control offers -- the visibility floor, the
// colour wheel, the volume, the overlap distance, the nudge limit -- and which
// settings are on/off switches. Nothing here restates one of those numbers; a
// JS copy of the nudge limit once said ten seconds while the server said twenty.

/**
 * THE WORDS FOR EACH ON/OFF SETTING, one row per `switch(...)` row in
 * ghost/settings.py (the server lists them in `limits.switches`). The renderer
 * draws each of them. A switch the server lists without a row here is not
 * drawn, and tests/test_ghost_settings.py fails on it.
 *
 * - shadow: his A/B switch for the game's own shadow under Ghost Mario
 *   (2026-09-22).
 * - occlusion_fade: the player-first fade (2026-09-23): where Ghost Mario
 *   stands between the camera and you it goes faint over your Mario, and it
 *   fades out right at the camera.
 * - sound_fade (2026-09-28): the ghost's sound fades whenever its picture
 *   does (overlapping you, in front of you, at the camera), and a sound you
 *   both make on the same frame while it overlaps you plays once. It sits
 *   beside Volume, the sound's other control.
 * - hud_inputs_enabled (2026-10-02): each ghost's inputs stacked above your
 *   own input display in the bottom-left corner. Its own switch, beside
 *   Inputs, because it is a different place to read them, not a style of them.
 * - record_ghosts (2026-10-02, a second copy since 2026-10-03): while it is on
 *   the trainer also records a ghost copy, the game window as you saw it, and
 *   the replay's Show ghosts plays it. Your own recording stays clean. Off at
 *   first; it applies to what is recorded from then on.
 * - path_enabled and path_buttons (2026-10-03): the ghost's path, a line in
 *   the game world along the route the ghosts take (the fastest one's, or their
 *   average), and each button
 *   press drawn where it was pressed. The path and presses are on at first.
 *   Path buttons, Path from and Tune path only
 *   mean something while the path shows, so they nest beneath it.
 */
export const SWITCH_LABELS = {
  names_enabled: { name: "Names", className: "ghost-names", group: "Ghost names",
    label: "Show whose run each ghost is playing",
    waiting: "Waiting for the ghost name setting" },
  inputs_enabled: { name: "Inputs", className: "ghost-inputs", group: "Ghost inputs",
    label: "Show each ghost's recorded controller inputs",
    waiting: "Waiting for the ghost input setting" },
  hud_inputs_enabled: { name: "Inputs in corner", className: "ghost-hud-inputs",
    group: "Ghost inputs in the corner",
    label: "Stack each ghost's inputs above your own input display in the bottom-left corner",
    waiting: "Waiting for the corner input setting" },
  shadow: { name: "Shadow", className: "ghost-shadow", group: "Ghost shadow",
    label: "Draw Mario's own shadow under the ghost",
    waiting: "Waiting for the ghost's shadow setting" },
  occlusion_fade: { name: "Fade in front of Mario", className: "ghost-front-fade",
    group: "Fade the ghost in front of Mario",
    label: "Fade the ghost where it covers your Mario or the camera",
    waiting: "Waiting for the fade-in-front setting" },
  sound_fade: { name: "Fade its sound", className: "ghost-sound-fade",
    group: "Fade the ghost's sound with its picture",
    label: "Its sound fades whenever its picture does",
    waiting: "Waiting for the ghost's sound setting" },
  record_ghosts: { name: "Ghosts in recording", className: "ghost-record",
    group: "Ghosts in the recording",
    label: "Also record a copy of your replays with the ghosts in it, as the game window shows them",
    waiting: "Waiting for the recording setting" },
  path_enabled: { name: "Path", className: "ghost-path", group: "Ghost path",
    label: "Draw the route ahead of you that your ghosts take, on the ground and through the air",
    waiting: "Waiting for the ghost path setting" },
  path_buttons: { name: "Path buttons", className: "ghost-path-buttons", group: "Button presses on the path",
    label: "Show each button press on the path, where it was pressed",
    waiting: "Waiting for the path buttons setting" },
};

/** WHICH GHOSTS THE PATH IS DRAWN FROM, in the words the choice offers. His
 * ask, 2026-10-03: "it would be nice to test which approach feels better." */
export const PATH_SOURCE_LABELS = { fastest: "Fastest ghost", average: "Average of ghosts" };

/**
 * THE PATH'S FOUR TUNABLES, in the order the Tune path disclosure draws them.
 * `unit` is how the readout states the value; every range, step and default
 * is the server's (`limits.path_tuning`), never restated here.
 */
export const PATH_TUNING_LABELS = {
  ahead_s: { name: "How far ahead", unit: "s",
    label: "How many seconds of the ghost's run the path reaches ahead of you" },
  width: { name: "Width", unit: "units", label: "How wide the path is, in game units" },
  opacity: { name: "Strength", unit: "%", label: "How strongly the path is drawn" },
  spacing_s: { name: "Arrow spacing", unit: "s",
    label: "How many seconds of the ghost's run pass between two arrows; closer arrows mean slower" },
};

// One press is one game frame: the unit the ghost actually moves in, and the
// unit a frame map can express. Press and hold to run through them.
export const NUDGE_STEP = 1;

// A MISSING opacity is UNKNOWN, and unknown is `null` -- never a number.
//
// `Number(null)` is 0 and `Number("")` is 0, so a payload that had not said
// yet would otherwise clamp to the floor and draw the faintest possible ghost.
// The first version answered `OPACITY_MAX` for that instead, which is the
// mirror of the same mistake and the worse direction: the control then SHOWS
// "100%" for a value the server never sent, and a number on screen that
// nobody chose is one small drag away from being written back as though he
// had chosen it. The server owns both the default and his saved preference.
//
// So: unknown stays unknown, the control says so, and nothing sends it.
// `range` is a published `{min, max}` (limits.opacity); without one the
// value passes through, since only the server knows the control's floor.
export function clampOpacity(value, range = null) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return range ? Math.min(range.max, Math.max(range.min, number)) : number;
}

// `limit` is limits.nudge_frames.limit, whole game frames either way.
export function clampNudge(frames, limit = Infinity) {
  const number = Math.round(Number(frames));
  if (!Number.isFinite(number)) return 0;
  return Math.min(limit, Math.max(-limit, number));
}

// `range` is limits.tint: one turn of the colour wheel, half-open, because 0
// and 360 are the same colour and the switch beside it turns the shift off.
export function clampHue(degrees, range = null) {
  const number = Math.round(Number(degrees));
  const low = range ? range.min : 0;
  if (degrees === null || degrees === undefined || !Number.isFinite(number)) return low;
  return range ? Math.min(range.max, Math.max(low, number)) : number;
}

/**
 * The colour treatment as the server stated it, or null while nobody has said.
 *
 * UNKNOWN IS NOT "OFF", for exactly the reason unknown is not an opacity (see
 * clampOpacity): drawing the switch as off before the server has answered puts
 * a state on screen that the very next payload corrects, and one click from
 * there writes "off" back as though he had chosen it. The shipped default is
 * ON, so guessing wrong here would show him a plain ghost and tell him the
 * colour was something he had already turned off.
 */
export function ghostTint(tint, range = null) {
  if (!tint || typeof tint !== "object") return null;
  return { enabled: Boolean(tint.enabled), hue: clampHue(tint.hue_degrees, range) };
}

/** "+120°" -- the shift, for the readout beside the control. */
export function hueText(degrees) {
  return `+${clampHue(degrees)}°`;
}

/**
 * What the colour control SHOWS: the server's answer with whichever half he
 * has just touched laid over it, or null while nobody has said.
 *
 * `current` is `ghostControls().tint` and `draft` is the optimistic patch in
 * the WIRE's own shape (`{enabled, hue_degrees}`), because that is the thing
 * about to be sent. One merge, here, rather than one at each reader -- the
 * two halves are written by different gestures and either can be in flight
 * alone, so `"enabled" in draft` is the question, never its truthiness.
 */
export function shownTint(current, draft, range = null) {
  if (!current && !draft) return null;
  const base = current || { enabled: false, hue: clampHue(null, range) };
  return {
    enabled: draft && "enabled" in draft
      ? Boolean(draft.enabled) : base.enabled,
    hue: draft && "hue_degrees" in draft
      ? clampHue(draft.hue_degrees, range) : base.hue,
  };
}

/**
 * THE ON/OFF SETTINGS, by name: every switch the server lists in
 * `limits.switches` (ghost/settings.py `switch(...)` rows), each the bool it
 * stated or null. Null until the server says, like the tint: an unknown switch
 * is drawn unknown, never as off.
 */
export function ghostSwitches(session, limits) {
  const names = Array.isArray(limits?.switches) ? limits.switches : [];
  return Object.fromEntries(names.map(
    (name) => [name, typeof session?.[name] === "boolean" ? session[name] : null]));
}

/** The published ranges, or none from a server that has not said. */
export function ghostLimits(session) {
  const limits = session?.limits;
  return limits && typeof limits === "object" ? limits : {};
}

/** How far the nudge may go, whole game frames either way. */
export function nudgeLimit(limits) {
  return limits?.nudge_frames?.limit ?? Infinity;
}

// The values whose shape the published limits decide.
function limitedValues(session, limits) {
  return {
    opacity: clampOpacity(session.opacity, limits.opacity),
    tint: ghostTint(session.tint, limits.tint),
    switches: ghostSwitches(session, limits),
    nudge: clampNudge(session.nudge_frames, nudgeLimit(limits)),
    nameBox: ghostNameBox(session, limits),
    pathSource: ghostOption("path_source", session.path_source, limits),
    pathTuning: ghostPathTuning(session.path_tuning, limits),
  };
}

/**
 * The path's tunables as the server stated them, each clamped into its
 * published range, or null while nobody has said (unknown is not a thumb
 * parked at a default, the clampOpacity rule). `draft` is the in-flight
 * partial (`{width}`, say) laid over it, the way the colour's halves merge.
 */
export function ghostPathTuning(tuning, limits, draft = null) {
  const ranges = limits?.path_tuning;
  if (!ranges || !tuning || typeof tuning !== "object") return null;
  const shown = { ...tuning, ...(draft || {}) };
  return Object.fromEntries(Object.keys(ranges).map(
    (name) => [name, clampOpacity(shown[name], ranges[name])]));
}

/** The decimals a slider's step shows: 0.5 -> 1, 0.05 -> 2, 5 -> 0. */
function stepDigits(step) {
  return (String(step ?? "").split(".")[1] || "").length;
}

/** "3.0 s", "40 units", "85%" -- a path tunable's readout, or a dash while unknown. */
export function pathTuningText(name, value, range) {
  const words = PATH_TUNING_LABELS[name];
  if (value === null || value === undefined || !words) return "—";
  if (words.unit === "%") return `${Math.round(value * 100)}%`;
  return `${Number(value).toFixed(stepDigits(range?.step))} ${words.unit}`;
}

/** A choice setting's value when the server publishes it among `limits[name].options`, else null. */
export function ghostOption(name, value, limits) {
  const options = limits?.[name]?.options;
  return Array.isArray(options) && options.includes(value) ? value : null;
}

/** The box behind each ghost's name: its colour and how solid it is, each null until the server says. */
export function ghostNameBox(source, limits) {
  const range = limits?.name_background_opacity;
  return {
    background: ghostOption("name_background", source?.name_background, limits),
    opacity: range ? clampOpacity(source?.name_background_opacity, range) : null,
  };
}

/**
 * A session target ({kind, entity_key, strat}) as a practice card's own
 * entity key, or null.
 *
 * Tolerant of BOTH spellings on purpose: the server side of I5 is being
 * built in parallel, so this accepts the template spelling ("8-1") and the
 * card spelling ("star:8:1") and answers the card's. A server that already
 * sends the card's key needs no change here; one that sends the template's
 * is translated once, here, rather than at each reader.
 */
export function ghostTargetKey(target) {
  if (!target || !target.kind || target.entity_key == null) return null;
  const kind = String(target.kind);
  const key = String(target.entity_key);
  if (key.startsWith(`${kind}:`)) return key;
  if (kind === "segment") return `segment:${key}`;
  if (kind !== "star") return null;
  return `star:${key.replace("-", ":")}`;
}

/**
 * Everything the control draws, from the session and the card it sits on.
 *
 * `session` is the I5 payload, or null while the first read is in flight.
 * `entityKey` is the hosting card's own key.
 *
 * Returns:
 *   known       the session is answering about THIS entity
 *   available   that entity has a native ghost to play alongside you
 *   enabled     the switch is on
 *   canToggle   the switch may be flipped at all
 *   reasonText  why it may not be -- never null while canToggle is false
 *   showsTuning the opacity and timing controls belong on screen
 *   opacity     0..1 as the SERVER stated it, already clamped -- or null
 *               while nobody has said, which the control draws as unknown and
 *               never writes back
 *   tint        {enabled, hue} as the SERVER stated it -- or null, same rule
 *   nudge       whole game frames, already clamped
 */
function ghostReason(session, source) {
  if (!source.available) return source.reason_text || "There is no run to play alongside you here yet.";
  return session.enabled ? session.window?.reason || null : null;
}

/** Appearance preferences also exist before a practice target is selected. */
export function ghostAppearanceControls(session) {
  if (!session || typeof session !== "object") return null;
  const limits = ghostLimits(session);
  return {
    ...limitedValues(session, limits),
    volume: typeof session.volume === "number" ? session.volume : null,
    limits,
    proximity: session.proximity || null,
  };
}

/** Keep successive edits to one preference together until the modal commits. */
export function mergeGhostAppearance(previous, patch) {
  const changes = typeof patch === "function" ? patch(previous) : patch;
  const next = {...previous, ...changes};
  for (const key of ["tint", "proximity", "path_tuning"]) {
    if (changes[key]) next[key] = {...previous[key], ...changes[key]};
  }
  return next;
}

export function ghostControls(session, entityKey) {
  const waiting = {
    known: false, available: false, enabled: false, canToggle: false,
    // Not an error and not a refusal -- the honest third state, and it has
    // to say something, because a disabled control with no reason is the
    // dead button this project has already shipped once.
    reasonText: "Checking whether this has a run to play alongside you...",
    // Nobody has said how solid the ghost is drawn, so the control does not
    // pretend to know -- see clampOpacity.
    showsTuning: false, opacity: null, tint: null, proximity: null, switches: {},
    volume: null, limits: {},
    nameBox: { background: null, opacity: null }, pathSource: null, pathTuning: null,
    nudge: 0,
    selection: null,
  };
  if (!session || typeof session !== "object") return waiting;
  const targetKey = ghostTargetKey(session.target);
  if (!targetKey || (entityKey && targetKey !== entityKey)) return waiting;
  const source = session.source || {};
  const available = Boolean(source.available);
  const enabled = Boolean(session.enabled) && available;
  return {
    known: true,
    available,
    enabled,
    canToggle: available,
    reasonText: ghostReason(session, source),
    showsTuning: enabled,
    ...ghostAppearanceControls(session),
    selection: session.selection || null,
  };
}

/** WHICH GHOSTS PLAY: a set of ticks, his PB first (`ghost/selection.py`).
 * The PB box stays tickable with no PB saved yet -- ticking it is what makes
 * his NEXT PB appear. A saved recording that cannot play as a ghost can
 * be unticked but not ticked; a ticked one that was deleted stays listed so
 * it can be unticked rather than silently vanishing.
 */
export function ghostRecordingOptions(selection, draft = {}, strat = null) {
  if (!selection) return null;
  const choice = shownChoice(selection, draft);
  const candidates = (selection.candidates || []).filter((candidate) => candidateShown(candidate, choice));
  const hues = new Map((selection.playing || []).map((row) => [row.key, row.hue_degrees]));
  const items = [pbItem(selection.pb, choice.pb, hues)];
  const unplayable = [];
  for (const candidate of candidates) {
    const checked = candidateChecked(candidate, choice);
    if (!checked && neverPlaysAsMario(candidate)) {
      unplayable.push(candidate);
      continue;
    }
    items.push(candidateItem(candidate, checked, hues, strat));
  }
  const missing = [...choice.attempts, ...(choice.imports || [])].filter(
    (id) => !candidates.some((candidate) => (candidate.import_id || candidate.attempt_id) === id));
  for (const id of missing) {
    items.push({ key: id, checked: true, disabled: false, hue: null,
      label: "Removed ghost", reason: "no longer saved" });
  }
  return {
    items, choice, reasonText: choiceReason(choice, missing),
    folded: foldedLine(unplayable),
    toggle: (key) => toggled(choice, key),
  };
}

function candidateChecked(candidate, choice) {
  return candidate.import_id ? (choice.imports || []).includes(candidate.import_id)
    : choice.attempts.includes(candidate.attempt_id);
}

function candidateShown(candidate, choice) {
  return !candidate.practice || candidateChecked(candidate, choice);
}

/** The server's reasons a run can NEVER play as a ghost: its poses are in
 * an older format, or were never recorded or kept. Nothing he does on this
 * card changes that, so those runs fold into one quiet line
 * instead of a column of disabled boxes each saying the same thing (his
 * picker, 2026-09-23). A ticked one stays a box so it can be unticked. */
const NEVER_PLAYS_AS_MARIO = new Set(["older_pose_format", "no_native_poses", "native_preservation_failed"]);

function neverPlaysAsMario(candidate) {
  return !candidate.native_available && NEVER_PLAYS_AS_MARIO.has(candidate.native_reason);
}

function foldedLine(rows) {
  if (!rows.length) return null;
  const reasons = [...new Set(rows.map((row) => row.native_reason_text).filter(Boolean))];
  return {
    count: rows.length,
    label: `${rows.length} older run${rows.length === 1 ? "" : "s"} can't play as ghosts`,
    reason: reasons.join(" "),
  };
}

function shownChoice(selection, draft) {
  const choice = Object.hasOwn(draft, "ghosts") ? draft.ghosts : selection.ghosts;
  return { pb: choice?.pb !== false, attempts: [...(choice?.attempts || [])],
    ...(choice?.imports?.length ? { imports: [...choice.imports] } : {}) };
}

function toggled(choice, key) {
  if (key === "pb") return { ...choice, pb: !choice.pb };
  if (typeof key === "string") {
    const imports = (choice.imports || []).includes(key)
      ? choice.imports.filter((id) => id !== key) : [...(choice.imports || []), key];
    return { ...choice, imports };
  }
  const attempts = choice.attempts.includes(key)
    ? choice.attempts.filter((id) => id !== key) : [...choice.attempts, key];
  return { ...choice, attempts };
}

function pbItem(pb, checked, hues) {
  if (!pb) return { key: "pb", checked, disabled: false, hue: null,
    label: "Your PB", reason: "none saved yet; your next PB appears here" };
  const available = Boolean(pb.native_available);
  return { key: "pb", checked, disabled: false, hue: checked ? hues.get("pb") ?? null : null,
    label: `Your PB · ${runName(pb)}`,
    reason: available ? gapReason(pb) : pb.native_reason_text };
}

/** Frames where Mario's position was not recorded: the ghost blinks there. */
function gapReason(run) {
  const gaps = run.blinks || 0;
  return gaps ? `blinks on ${gaps} frame${gaps === 1 ? "" : "s"} with no recorded position` : null;
}

/** A run is named by the time it achieved -- `17"80` -- which is what he
 * remembers it by (his ask, 2026-09-22: "Attempt #1906 means nothing"). */
function runName(run) {
  return run.frames == null ? (run.import_id ? "Imported ghost" : `Attempt #${run.attempt_id}`)
    : fmtIgtShort(run.frames);
}

function candidateItem(candidate, checked, hues, strat) {
  const available = Boolean(candidate.native_available);
  // The strategy only when it is not the one being practised: every row on
  // this card already is, and repeating it is noise.
  const other = candidate.strat && candidate.strat !== strat ? candidate.strat : null;
  const key = candidate.import_id || candidate.attempt_id;
  const author = candidate.import_id ? candidate.author || "Uncredited" : null;
  return {
    key, checked, disabled: !available && !checked,
    hue: checked ? hues.get(key) ?? null : null,
    label: [runName(candidate), author, other].filter(Boolean).join(" · "),
    reason: available ? gapReason(candidate) : candidate.native_reason_text || "No saved Mario poses",
  };
}

function choiceReason(choice, missing) {
  if (missing.length) return "A ticked ghost is no longer saved. Untick it or choose another.";
  if (!choice.pb && !choice.attempts.length && !choice.imports?.length) return "Tick a ghost to race it here.";
  return null;
}

/** "60%" -- what the slider is showing, for the readout beside it. An unknown
 *  opacity reads as a dash: a percentage nobody chose is a claim. */
export function opacityText(opacity) {
  const value = clampOpacity(opacity);
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

/**
 * The timing readout, in the words a player would use rather than a signed
 * number. Positive means the ghost is shown further along its own run than
 * you are in yours, which on screen is the ghost being ahead of you.
 */
export function nudgeText(frames) {
  const value = clampNudge(frames);
  if (value === 0) return "with you";
  const count = Math.abs(value);
  const unit = count === 1 ? "frame" : "frames";
  return `${count} ${unit} ${value > 0 ? "ahead" : "behind"}`;
}

/** The same offset as a duration, for the control's own hover text. */
export function nudgeSeconds(frames) {
  return clampNudge(frames) / GAME_FPS;
}

/**
 * The newest ghost_changed payload in the store's raw websocket feed, or
 * null. The feed is newest-first and capped, so this is a scan to the first
 * hit, and the object it returns is the one the socket handler stored --
 * stable across renders, which is what lets an effect depend on it and run
 * only when a genuinely new one lands.
 */
export function newestGhostSession(feed) {
  for (const event of feed || []) {
    if (event && event.type === "ghost_changed" && event.payload) {
      return event.payload;
    }
  }
  return null;
}
