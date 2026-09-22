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

// A ghost you cannot see through is not a ghost, and one at zero is a
// control that looks broken -- so the slider's own floor is well above
// invisible. The server clamps too; this is what the control offers.
export const OPACITY_MIN = 0.15;
export const OPACITY_MAX = 1;

// THE COLOUR WHEEL, one full turn. The ghost's whole picture is shifted this
// far around it, which is what makes it unmistakable against the live game.
// 0 and 360 are the same colour, so the control offers the half-open range --
// and 0 is a real choice (no shift) rather than a hole in the range, because
// the switch beside it is what turns the treatment off.
export const HUE_MIN = 0;
export const HUE_MAX = 359;

// WHAT THE GHOST SHOWS. His report is why there is a choice: *"it's still
// extremely hard to understand the ghost, because all the camera movement /
// scenery overlaps."* The whole recorded screen is the ghost he already knows;
// only-Mario draws the recorded Mario pose into the live game.
//
// The LABELS say what each one does rather than naming the technique, and they
// are the words the control offers -- there is no second list anywhere.
export const GHOST_VIEWS = ["whole", "mario"];
export const VIEW_LABELS = { whole: "Whole picture", mario: "Only Mario" };

// One press is one game frame: the unit the ghost actually moves in, and the
// unit a frame map can express. Press and hold to run through them.
export const NUDGE_STEP = 1;
// Ten seconds either way. Past that a "nudge" is a different run.
export const NUDGE_LIMIT = 300;

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
export function clampOpacity(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.min(OPACITY_MAX, Math.max(OPACITY_MIN, number));
}

export function clampNudge(frames) {
  const number = Math.round(Number(frames));
  if (!Number.isFinite(number)) return 0;
  return Math.min(NUDGE_LIMIT, Math.max(-NUDGE_LIMIT, number));
}

export function clampHue(degrees) {
  const number = Math.round(Number(degrees));
  if (!Number.isFinite(number)) return HUE_MIN;
  return Math.min(HUE_MAX, Math.max(HUE_MIN, number));
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
export function ghostTint(tint) {
  if (!tint || typeof tint !== "object") return null;
  return { enabled: Boolean(tint.enabled), hue: clampHue(tint.hue_degrees) };
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
export function shownTint(current, draft) {
  if (!current && !draft) return null;
  const base = current || { enabled: false, hue: HUE_MIN };
  return {
    enabled: draft && "enabled" in draft
      ? Boolean(draft.enabled) : base.enabled,
    hue: draft && "hue_degrees" in draft
      ? clampHue(draft.hue_degrees) : base.hue,
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
    showsTuning: false, opacity: null, tint: null, view: null, proximity: null, shadow: null,
    marioBoxes: { available: false, reasonText: null }, nudge: 0,
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
    opacity: clampOpacity(session.opacity),
    tint: ghostTint(session.tint),
    // Null until the server says, like the tint: an unknown switch is drawn
    // unknown, never as off.
    shadow: typeof session.shadow === "boolean" ? session.shadow : null,
    proximity: session.proximity || null,
    view: GHOST_VIEWS.includes(session.view) ? session.view : null,
    ...viewAvailability(source),
    nudge: clampNudge(session.nudge_frames),
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
  const candidates = selection.candidates || [];
  const hues = new Map((selection.playing || []).map((row) => [row.key, row.hue_degrees]));
  const items = [pbItem(selection.pb, choice.pb, field, hues)];
  for (const candidate of candidates) {
    items.push(candidateItem(candidate, choice.attempts.includes(candidate.attempt_id), field, view, hues, strat));
  }
  const missing = choice.attempts.filter(
    (id) => !candidates.some((candidate) => candidate.attempt_id === id));
  for (const id of missing) {
    items.push({ key: id, checked: true, disabled: false, hue: null,
      label: "Removed ghost", reason: "no longer saved" });
  }
  const rows = selection.pb ? [selection.pb, ...candidates] : candidates;
  return {
    items, choice, reasonText: choiceReason(choice, missing, view),
    toggle: (key) => toggled(choice, key),
    views: {
      mario: rows.some((row) => row.native_available),
      whole: rows.some((row) => row.whole_available),
    },
  };
}

function shownChoice(selection, draft) {
  const choice = Object.hasOwn(draft, "ghosts") ? draft.ghosts : selection.ghosts;
  return { pb: choice?.pb !== false, attempts: [...(choice?.attempts || [])] };
}

function toggled(choice, key) {
  if (key === "pb") return { ...choice, pb: !choice.pb };
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
  const gaps = field === "native" ? run.pose_gaps || 0 : 0;
  return gaps ? `blinks on ${gaps} frame${gaps === 1 ? "" : "s"} with no recorded position` : null;
}

/** A run is named by the time it achieved -- `17"80` -- which is what he
 * remembers it by (his ask, 2026-09-22: "Attempt #1906 means nothing"). */
function runName(run) {
  return run.frames == null ? `Attempt #${run.attempt_id}` : fmtIgtShort(run.frames);
}

function candidateItem(candidate, checked, field, view, hues, strat) {
  const available = Boolean(candidate[`${field}_available`]);
  // The strategy only when it is not the one being practised: every row on
  // this card already is, and repeating it is noise.
  const other = candidate.strat && candidate.strat !== strat ? candidate.strat : null;
  return {
    key: candidate.attempt_id, checked, disabled: !available && !checked,
    hue: checked ? hues.get(candidate.attempt_id) ?? null : null,
    label: [runName(candidate), other].filter(Boolean).join(" · "),
    reason: available ? gapReason(candidate, field) : candidate[`${field}_reason_text`]
      || (view === "mario" ? "No saved Mario poses" : "No saved video"),
  };
}

function choiceReason(choice, missing, view) {
  if (missing.length) return "A ticked ghost is no longer saved. Untick it or choose another.";
  if (!choice.pb && !choice.attempts.length) return "Tick a ghost to race it here.";
  if (view === "whole" && choice.pb + choice.attempts.length > 1) {
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
