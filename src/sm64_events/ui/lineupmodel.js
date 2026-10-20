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
//     (round 5: "I like the strategy buttons in the row"), every one of them,
//     in the order the server sends: the rank standards table's (round 10:
//     "we have the realestate to show more of the strategies").
//   * Loading: your seat plus empty places, so the card never jumps.
//   * Ready: you first, then the rivals; a seat still downloading is drawn
//     with a dashed ring (`pending`).
//   * Nobody or offline: your seat and one sentence.
import { RANK_FLOOR } from "./components/caps.js";

export const LINEUP_WORDS = {
  noStrategy: "Pick your strategy to race ghosts",
  noStrategyOff: "Pick your strategy",
  noStrategies: "Create a strategy to race ghosts",
  nobody: "No ghosts available.",
  offline: "Ghosts return when you're back online.",
  signedOut: "Sign in to race community ghosts.",
  unknownRom: "Load a compatible practice ROM to race ghosts.",
  unreachable: "Ghost controls couldn't load.",
};

function strategyChoices(strategies) {
  return (strategies || []).filter(name => typeof name === "string" && name)
    .map(name => ({ label: name, strategy: name }));
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

// A pinned run of your own says so by identity (`own`, from the server), never
// by whatever name a recording carries -- an import credited "You" is not you.
function rivalSeat(row) {
  return { key: `s${row.slot}`, id: row.id, name: row.own === true ? "Your run" : (row.player_name || "Chosen ghost"),
    frames: Number.isInteger(row.frames) ? row.frames : null, tier: row.tier, division: row.division,
    hue: row.hue_degrees, pinned: Boolean(row.pinned), pending: Boolean(row.pending),
    verified: row.verified === true, slot: row.slot };
}

const EMPTY_WORDS = { offline: LINEUP_WORDS.offline, signed_out: LINEUP_WORDS.signedOut };

/**
 * No strategy: nobody to race, so the row offers the strategies themselves.
 * The buttons pick the card's strategy, which matters with ghosts off too, so
 * this note always stands open (round 11: "That strategy selector should be
 * visible REGARDLESS of ghosts being on or off"); with ghosts off it drops
 * "to race ghosts", since picking one races nobody until they are on.
 */
function noStrategyModel(on, entityKey, strategies) {
  const choices = strategyChoices(strategies);
  const text = on ? LINEUP_WORDS.noStrategy : LINEUP_WORDS.noStrategyOff;
  return { on: true, lineup: `${entityKey}|none`, people: [],
    note: choices.length ? { text, choices } : { text: LINEUP_WORDS.noStrategies } };
}

/** Loading: your seat, then one empty place per rival the options ask for. */
function loadingModel(on, lineup, you, rivals) {
  const count = Math.max(1, Math.min(4, rivals.options?.count || 4));
  const holes = Array.from({ length: count }, (_, slot) => ({ key: `s${slot}`, id: `s${slot}`, placeholder: true,
    frames: null, hue: null }));
  return { on, lineup, people: [you, ...holes], loading: true };
}

/**
 * Ready: you and the rivals, or you and one sentence when nobody can be raced.
 * No seats and no reason means nobody has been asked yet (the overlay was
 * off): that is about to load, so it draws as loading, never as "nobody".
 */
function readyModel(on, lineup, you, rivals) {
  const seats = (rivals.rows || []).filter(row => Number.isInteger(row.slot)).map(rivalSeat);
  if (seats.length) return { on, lineup, people: [you, ...seats] };
  if (!rivals.reason) return loadingModel(on, lineup, you, rivals);
  return { on, lineup, people: [you], note: { text: EMPTY_WORDS[rivals.reason] || LINEUP_WORDS.nobody } };
}

/** What a card shows when it cannot ask about rivals at all, or null when it can. */
function blockedModel({ session, known, reachable, on, entityKey }) {
  if (!reachable) {
    return { on: true, lineup: `${entityKey}|unreachable`, people: [],
      note: { text: LINEUP_WORDS.unreachable, action: "Retry", kind: "retry" } };
  }
  if (!known || !session) return undefined;
  if (session.source?.reason === "unknown_rom") {
    return { on, lineup: `${entityKey}|rom`, people: [], note: { text: LINEUP_WORDS.unknownRom } };
  }
  return null;
}

/**
 * The model for one practice card, or null while the session has not yet
 * answered for this card (nothing is drawn rather than a guess).
 * `on` is the overlay switch as shown (the in-flight draft laid over the server).
 */
export function lineupModel({ session, known, reachable = true, on, entityKey, strategies }) {
  const blocked = blockedModel({ session, known, reachable, on, entityKey });
  if (blocked !== null) return blocked ?? null;
  const rivals = session.rivals;
  if (!rivals) return null;
  const strat = session.target?.strat || null;
  if (rivals.state === "no_strategy" || !strat) return noStrategyModel(on, entityKey, strategies);
  const lineup = `${entityKey}|${strat}|${rivals.scope || "strategy"}`;
  const you = youSeat(rivals);
  return rivals.state === "loading" ? loadingModel(on, lineup, you, rivals) : readyModel(on, lineup, you, rivals);
}
