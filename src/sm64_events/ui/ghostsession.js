// src/sm64_events/ui/ghostsession.js
//
// The rules behind the practice card's Ghost Overlay control. No DOM, no
// fetch, no preact -- only the shared time format -- so node drives every rule
// below directly (tests/test_ui_ghost_session.py) instead of a browser.
//
// The server side is ONE session for the whole app (interface I5): it knows
// whether the overlay is on, how solid it is drawn, how many game frames the
// ghost is nudged by, which target it is following, and whether that target
// has any footage to play. The card only ever draws that answer.
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

// WHAT THE GHOST SHOWS. His report is why there is a choice: *"it's still
// extremely hard to understand the ghost, because all the camera movement /
// scenery overlaps."* The whole recorded screen is the ghost he already knows;
// only-Mario draws the recorded Mario pose into the live game.
//
// The LABELS say what each one does rather than naming the technique, and they
// are the words the control offers -- there is no second list anywhere.
export const GHOST_VIEWS = ["whole", "mario"];
export const VIEW_LABELS = { whole: "Whole picture", mario: "Only Mario" };

/**
 * THE WORDS FOR EACH ON/OFF SETTING, one row per `switch(...)` row in
 * ghost/settings.py (the server lists them in `limits.switches`). Only Mario
 * draws each of them. A switch the server lists without a row here is not
 * drawn, and tests/test_ghost_settings.py fails on it.
 *
 * - shadow: his A/B switch for the game's own shadow under Ghost Mario
 *   (2026-09-22); the whole picture already contains the recorded one.
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
// had chosen it. His stored ghost opacity is 1.0 while the server's default
// has always been 0.5, and this is the shape that could put it there.
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
  };
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
 *   available   that entity has footage to play alongside you
 *   enabled     the switch is on
 *   canToggle   the switch may be flipped at all
 *   reasonText  why it may not be -- never null while canToggle is false
 *   showsTuning the opacity and timing controls belong on screen
 *   opacity     0..1 as the SERVER stated it, already clamped -- or null
 *               while nobody has said, which the control draws as unknown and
 *               never writes back
 *   tint        {enabled, hue} as the SERVER stated it -- or null, same rule
 *   view        "whole" or "mario" as the SERVER stated it -- or null, same
 *               rule again: a control that guesses is a control that writes
 *               its guess back the first time he touches it
 *   marioBoxes  whether this ghost CAN be drawn as Mario alone, and the
 *               server's own sentence saying why not
 *   nudge       whole game frames, already clamped
 */
function viewAvailability(source) {
  return {
    nativeViews: typeof source.native_available === "boolean" ? {
      mario: source.native_available,
      whole: Boolean(source.whole_available),
      marioReason: source.native_reason_text,
      wholeReason: source.whole_reason_text,
    } : null,
    marioBoxes: {
      available: Boolean(source.native_available ?? source.mario_boxes_available),
      reasonText: (typeof source.native_available === "boolean"
        ? source.native_reason_text : source.mario_boxes_reason_text) || null,
    },
  };
}

function ghostReason(session, source) {
  if (!source.available) return source.reason_text || "There is no run to play alongside you here yet.";
  return session.enabled && session.view === "mario" ? session.window?.reason || null : null;
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
    showsTuning: false, opacity: null, tint: null, view: null, proximity: null, switches: {},
    volume: null, limits: {},
    nameBox: { background: null, opacity: null },
    marioBoxes: { available: false, reasonText: null }, nudge: 0,
    selection: null,
  };
  if (!session || typeof session !== "object") return waiting;
  const targetKey = ghostTargetKey(session.target);
  if (!targetKey || (entityKey && targetKey !== entityKey)) return waiting;
  const source = session.source || {};
  const available = Boolean(source.available);
  const enabled = Boolean(session.enabled) && available;
  const limits = ghostLimits(session);
  return {
    known: true,
    available,
    enabled,
    canToggle: available,
    reasonText: ghostReason(session, source),
    showsTuning: enabled,
    ...limitedValues(session, limits),
    volume: typeof session.volume === "number" ? session.volume : null,
    limits,
    proximity: session.proximity || null,
    view: GHOST_VIEWS.includes(session.view) ? session.view : null,
    ...viewAvailability(source),
    selection: session.selection || null,
  };
}

/** WHICH GHOSTS PLAY: a set of ticks, his PB first (`ghost/selection.py`).
 * The PB box stays tickable with no PB saved yet -- ticking it is what makes
 * his NEXT PB appear. A saved recording that cannot show this Show mode can
 * be unticked but not ticked; a ticked one that was deleted stays listed so
 * it can be unticked rather than silently vanishing.
 */
export function ghostRecordingOptions(selection, view, draft = {}, strat = null) {
  if (!selection || !GHOST_VIEWS.includes(view)) return null;
  const choice = shownChoice(selection, draft);
  const field = view === "mario" ? "native" : "whole";
  const candidates = (selection.candidates || []).filter((candidate) => candidateShown(candidate, choice));
  const hues = new Map((selection.playing || []).map((row) => [row.key, row.hue_degrees]));
  const items = [pbItem(selection.pb, choice.pb, field, hues)];
  const unplayable = [];
  for (const candidate of candidates) {
    const checked = candidateChecked(candidate, choice);
    if (view === "mario" && !checked && neverPlaysAsMario(candidate)) {
      unplayable.push(candidate);
      continue;
    }
    items.push(candidateItem(candidate, checked, field, view, hues, strat));
  }
  const missing = [...choice.attempts, ...(choice.imports || [])].filter(
    (id) => !candidates.some((candidate) => (candidate.import_id || candidate.attempt_id) === id));
  for (const id of missing) {
    items.push({ key: id, checked: true, disabled: false, hue: null,
      label: "Removed ghost", reason: "no longer saved" });
  }
  const rows = selection.pb ? [selection.pb, ...candidates] : candidates;
  return {
    items, choice, reasonText: choiceReason(choice, missing, view),
    folded: foldedLine(unplayable),
    toggle: (key) => toggled(choice, key),
    views: {
      mario: rows.some((row) => row.native_available),
      whole: rows.some((row) => row.whole_available),
    },
  };
}

function candidateChecked(candidate, choice) {
  return candidate.import_id ? (choice.imports || []).includes(candidate.import_id)
    : choice.attempts.includes(candidate.attempt_id);
}

function candidateShown(candidate, choice) {
  return !candidate.practice || candidateChecked(candidate, choice);
}

/** The server's reasons a run can NEVER play as Only Mario: its poses are in
 * an older format, or were never recorded or kept. Nothing he does on this
 * card changes that, so in Only Mario those runs fold into one quiet line
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
    label: `${rows.length} older run${rows.length === 1 ? "" : "s"} can't play as Only Mario`,
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

function pbItem(pb, checked, field, hues) {
  if (!pb) return { key: "pb", checked, disabled: false, hue: null,
    label: "Your PB", reason: "none saved yet; your next PB appears here" };
  const available = Boolean(pb[`${field}_available`]);
  return { key: "pb", checked, disabled: false, hue: checked ? hues.get("pb") ?? null : null,
    label: `Your PB · ${runName(pb)}`,
    reason: available ? gapReason(pb, field) : pb[`${field}_reason_text`] };
}

/** Frames where Mario's position was not recorded: the ghost blinks there.
 * Only Mario reads positions, so only that view says so. */
function gapReason(run, field) {
  const gaps = field === "native" ? run.blinks || 0 : 0;
  return gaps ? `blinks on ${gaps} frame${gaps === 1 ? "" : "s"} with no recorded position` : null;
}

/** A run is named by the time it achieved -- `17"80` -- which is what he
 * remembers it by (his ask, 2026-09-22: "Attempt #1906 means nothing"). */
function runName(run) {
  return run.frames == null ? (run.import_id ? "Imported ghost" : `Attempt #${run.attempt_id}`)
    : fmtIgtShort(run.frames);
}

function candidateItem(candidate, checked, field, view, hues, strat) {
  const available = Boolean(candidate[`${field}_available`]);
  // The strategy only when it is not the one being practised: every row on
  // this card already is, and repeating it is noise.
  const other = candidate.strat && candidate.strat !== strat ? candidate.strat : null;
  const key = candidate.import_id || candidate.attempt_id;
  const author = candidate.import_id ? candidate.author || "Uncredited" : null;
  return {
    key, checked, disabled: !available && !checked,
    hue: checked ? hues.get(key) ?? null : null,
    label: [runName(candidate), author, other].filter(Boolean).join(" · "),
    reason: available ? gapReason(candidate, field) : candidate[`${field}_reason_text`]
      || (view === "mario" ? "No saved Mario poses" : "No saved video"),
  };
}

function choiceReason(choice, missing, view) {
  if (missing.length) return "A ticked ghost is no longer saved. Untick it or choose another.";
  if (!choice.pb && !choice.attempts.length && !choice.imports?.length) return "Tick a ghost to race it here.";
  if (view === "whole" && choice.pb + choice.attempts.length + (choice.imports?.length || 0) > 1) {
    return "Whole picture shows the first ticked ghost; Only Mario shows them all.";
  }
  return null;
}

/** Let another saved recording offer a mode the current source cannot show. */
export function ghostViewChoices(nativeViews, recording) {
  if (!nativeViews || !recording) return nativeViews;
  return {
    ...nativeViews,
    mario: nativeViews.mario || recording.views.mario,
    whole: nativeViews.whole || recording.views.whole,
  };
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
 * What the view control SHOWS: the server's answer, or the choice he has just
 * made and which has not come back yet. Null while nobody has said.
 *
 * The same optimistic-draft rule the colour follows, and it is its own
 * function for the same reason: the control has to be able to tell "the
 * server says whole" from "nobody has said", because only one of those may be
 * written back.
 */
export function shownView(current, draft) {
  if (GHOST_VIEWS.includes(draft)) return draft;
  return GHOST_VIEWS.includes(current) ? current : null;
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
