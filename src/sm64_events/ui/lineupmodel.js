// src/sm64_events/ui/lineupmodel.js — what the Lineup shows, from the ghost session.
//
// One pure function turns the session the server sends (GET /api/ghost and the
// ghost_changed broadcast, `session.rivals`) into the plain model
// ui/lineup.js draws. Import-free apart from the tier registry, so node can
// drive every state without a page (tests/test_ui_lineup_model.py).
//
// THE LINEUP COMES FROM THREE THINGS: the star or segment, the strategy, and
// your saved time on it (round 3: "our UI has to be responsive to all of the
// contexts"). Everything else only decides whether a ghost can race yet.
//   * No strategy: nobody to race, so the row offers the strategies themselves
//     (round 5: "I like the strategy buttons in the row").
//   * Loading: your seat plus empty places, so the card never jumps.
//   * Ready: you first, then the rivals; a seat still downloading is drawn
//     with a dashed ring (`pending`).
//   * Nobody or offline: your seat and one sentence.
import { RANK_FLOOR } from "./components/caps.js";

// More strategies than this fold into "Other…", which opens the picker.
export const MAX_STRATEGY_CHOICES = 3;
export const OTHER_STRATEGY = "Other…";

export const LINEUP_WORDS = {
  noStrategy: "Pick your strategy to race ghosts",
  noStrategies: "Create a strategy to race ghosts",
  nobody: "No one has shared a run on this strategy yet.",
  offline: "Rivals return when you're back online.",
  signedOut: "Sign in to race community ghosts.",
  unknownRom: "Load a compatible practice ROM to race ghosts.",
  unreachable: "Ghost controls couldn't load.",
};

function strategyChoices(strategies) {
  const names = (strategies || []).filter(name => typeof name === "string" && name);
  const shown = names.slice(0, MAX_STRATEGY_CHOICES).map(name => ({ label: name, strategy: name }));
  return names.length > MAX_STRATEGY_CHOICES ? [...shown, { label: OTHER_STRATEGY, strategy: null }] : shown;
}

/** Your own seat: always present, Capless 5 with no time until your first finish. */
export function youSeat(rivals) {
  const pb = rivals?.pb || null;
  const racing = Boolean(pb) && rivals?.options?.include_pb !== false && pb.available !== false;
  return { key: "you", id: "you", you: true, name: "You",
    frames: pb && Number.isInteger(pb.frames) ? pb.frames : null,
    tier: pb?.tier || RANK_FLOOR.tier, division: pb?.division || RANK_FLOOR.division,
    hue: pb?.hue_degrees ?? 250, ghost: racing };
}

function rivalSeat(row) {
  return { key: `s${row.slot}`, id: row.id, name: row.player_name || "Chosen rival",
    frames: Number.isInteger(row.frames) ? row.frames : null, tier: row.tier, division: row.division,
    hue: row.hue_degrees, pinned: Boolean(row.pinned), pending: Boolean(row.pending),
    verified: row.verified === true, slot: row.slot };
}

/**
 * The model for one practice card, or null while the session has not yet
 * answered for this card (nothing is drawn rather than a guess).
 * `on` is the overlay switch as shown (the in-flight draft laid over the server).
 */
export function lineupModel({ session, known, reachable = true, on, entityKey, strategies }) {
  if (!reachable) {
    return { on: true, lineup: `${entityKey}|unreachable`, people: [],
      note: { text: LINEUP_WORDS.unreachable, action: "Retry", kind: "retry" } };
  }
  if (!known || !session) return null;
  const source = session.source || {};
  if (source.reason === "unknown_rom") {
    return { on, lineup: `${entityKey}|rom`, people: [], note: { text: LINEUP_WORDS.unknownRom } };
  }
  const rivals = session.rivals;
  if (!rivals) return null;
  const strat = session.target?.strat || null;
  if (rivals.state === "no_strategy" || !strat) {
    const choices = strategyChoices(strategies);
    return { on, lineup: `${entityKey}|none`, people: [],
      note: choices.length ? { text: LINEUP_WORDS.noStrategy, choices } : { text: LINEUP_WORDS.noStrategies } };
  }
  const lineup = `${entityKey}|${strat}|${rivals.scope || "strategy"}`;
  const you = youSeat(rivals);
  if (rivals.state === "loading") {
    const count = Math.max(1, Math.min(4, rivals.options?.count || 4));
    const holes = Array.from({ length: count }, (_, slot) => ({ key: `s${slot}`, id: `s${slot}`, placeholder: true,
      frames: null, hue: null }));
    return { on, lineup, people: [you, ...holes], loading: true };
  }
  const seats = (rivals.rows || []).filter(row => Number.isInteger(row.slot)).map(rivalSeat);
  if (!seats.length && rivals.reason) {
    return { on, lineup, people: [you],
      note: { text: rivals.reason === "offline" ? LINEUP_WORDS.offline
        : rivals.reason === "signed_out" ? LINEUP_WORDS.signedOut : LINEUP_WORDS.nobody } };
  }
  if (!seats.length) return { on, lineup, people: [you], note: { text: LINEUP_WORDS.nobody } };
  return { on, lineup, people: [you, ...seats] };
}
