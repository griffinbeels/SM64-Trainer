// src/sm64_events/ui/ghostsession.js
//
// The rules behind the practice card's Ghost Overlay control. No DOM, no
// fetch, no preact -- import-free, so node drives every rule below directly
// (tests/test_ui_ghost_session.py) instead of a browser having to prove them.
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
export const GAME_FPS = 30;

// A ghost you cannot see through is not a ghost, and one at zero is a
// control that looks broken -- so the slider's own floor is well above
// invisible. The server clamps too; this is what the control offers.
export const OPACITY_MIN = 0.15;
export const OPACITY_MAX = 1;

// One press is one game frame: the unit the ghost actually moves in, and the
// unit a frame map can express. Press and hold to run through them.
export const NUDGE_STEP = 1;
// Ten seconds either way. Past that a "nudge" is a different run.
export const NUDGE_LIMIT = 300;

// `Number(null)` is 0 and `Number("")` is 0, so a MISSING opacity would
// otherwise clamp to the floor and draw the faintest possible ghost for a
// payload that simply had not said yet. Absent means fully visible.
export function clampOpacity(value) {
  if (value === null || value === undefined || value === "") return OPACITY_MAX;
  const number = Number(value);
  if (!Number.isFinite(number)) return OPACITY_MAX;
  return Math.min(OPACITY_MAX, Math.max(OPACITY_MIN, number));
}

export function clampNudge(frames) {
  const number = Math.round(Number(frames));
  if (!Number.isFinite(number)) return 0;
  return Math.min(NUDGE_LIMIT, Math.max(-NUDGE_LIMIT, number));
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
 *   opacity     0..1, already clamped
 *   nudge       whole game frames, already clamped
 */
export function ghostControls(session, entityKey) {
  const waiting = {
    known: false, available: false, enabled: false, canToggle: false,
    // Not an error and not a refusal -- the honest third state, and it has
    // to say something, because a disabled control with no reason is the
    // dead button this project has already shipped once.
    reasonText: "Checking whether this has a run to play alongside you...",
    showsTuning: false, opacity: OPACITY_MAX, nudge: 0,
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
    reasonText: available ? null : (source.reason_text
      || "There is no run to play alongside you here yet."),
    showsTuning: enabled,
    opacity: clampOpacity(session.opacity),
    nudge: clampNudge(session.nudge_frames),
  };
}

/** "60%" -- what the slider is showing, for the readout beside it. */
export function opacityText(opacity) {
  return `${Math.round(clampOpacity(opacity) * 100)}%`;
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
