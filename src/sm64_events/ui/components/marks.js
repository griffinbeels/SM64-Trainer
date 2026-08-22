import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

// THE caveat vocabulary — "this recorded time does not mean what the rank
// beside it implies", said once, for every surface that shows a saved time.
//
// Three separate findings converged on the same two surfaces (the practice
// card, which has room for words, and the quick-select cell, which is an
// icon-only badge with room for none), and the reason they get ONE module
// rather than three is the reason this repo has a written rule about it: two
// surfaces honestly computing the same fact and disagreeing is the
// divergent-duplication class, and three marks designed separately is how the
// card and the cell drift apart again three days later.
//
//   unattributed — round-4 item 2. The entity's current PB carries no
//                  strat_tag at all, so no strategy can claim it and the
//                  rank shown beside it is a floor, not a grade. The
//                  practice CARD already says this (views.py's
//                  `_section_banner` returns the "unattributed" sentinel and
//                  ranks.js never floors it); the quick-select CELL is
//                  driven by `_strat_rank`, which still falls straight
//                  through to the floor. Same fact, one surface behind.
//   old_clock    — round-3 ruling 6. The attempt was timed by a wall-frame
//                  delta (`Attempt.timed_by == "delta"`) even though its
//                  closing event type is one that WOULD carry Usamune's IGT
//                  today (`closed_by in core.events.IGT_BEARING_EVENT_TYPES`).
//                  Both clauses are load-bearing: 570 of 626 segment
//                  attempts are delta-timed and most are delta FOREVER — a
//                  castle movement closing on a `level_changed` has no
//                  Usamune number to be given, so its delta IS how that
//                  segment is measured and stays perfectly comparable.
//   grab_timed   — round-4 item 4. A star's time is the GRAB quantity, not
//                  the x-cam quantity a leaderboard accepts. True of every
//                  star row recorded before 2026-08-01 (no `igt_timed_at` in
//                  its journaled payload at all) and of a fresh row whose
//                  x-cam wait aborted (`igt_timed_at == "grab"` — savestate
//                  load, level change, IGT reset, or the 300-frame backstop).
//
// The KEY is computed server-side (tracking/views.py) and this module only
// knows how to draw one: the predicates read `timed_by`/`closed_by`/
// `igt_timed_at`, which are Python-side facts, and restating them in JS would
// be the second door rather than the shared one.
// `suppressFloor` belongs to the CAVEAT, not to how it is drawn, and getting
// that backwards would have been a real bug rather than a tidiness point.
// PracticeCell draws the ladder FLOOR when a rankable entity has no rank
// (user, 2026-07-30 — an unranked-but-rankable thing should read as "bottom of
// the ladder", not "not a thing that ranks"). Exactly ONE of these three makes
// that floor a lie:
//
//   * `unattributed` — no strategy can ever claim this PB, so the floor asserts
//     a concrete rank that contradicts the time printed beside it. That IS the
//     live report ("Bowser 1 shows PB 0'26"30, but the rank display clearly
//     shows Capless 5... this should never happen").
//   * `old_clock` / `grab_timed` — the rank is perfectly real; the caveat is
//     about what the time MEASURES, not about whether it can be graded.
//     Suppressing the floor here would delete a true rank to explain a
//     different fact, which is a second bug wearing the first one's fix.
export const CAVEATS = {
  grab_timed: {
    glyph: "!",
    short: "Grab-timed",
    // His wording, verbatim (2026-08-02). The rule is the second half: "not a
    // leaderboard-legal time" describes the row, "Only xcam is allowed" says
    // what would have made it one, which is the thing a reader can act on.
    sentence: "Timed at star grab, not xcam. Only xcam is allowed.",
    suppressFloor: false,
  },
  old_clock: {
    glyph: "≠",
    short: "Old clock",
    sentence: "Timed by wall-clock frames, not Usamune's IGT — not comparable to a fresh run",
    suppressFloor: false,
  },
  unattributed: {
    glyph: "?",
    short: "Unattributed",
    // The remedy changed with the PB gate (2026-08-20): you can no longer
    // "set a new PB" out of this state without first saying which strategy
    // the run was, because a PB is only saveable under the strategy being
    // practised. Tagging the run is now BOTH the fix and the thing that
    // brings the PB back, since `set_attempt_strat` retags the pb row with
    // it -- so the sentence names the one action that works.
    sentence: "Not attributed to a strategy, so no rank can claim it (set this run's strategy to rank it)",
    suppressFloor: true,
  },
};

export function caveatOf(key) {
  return key ? CAVEATS[key] || null : null;
}

// ---------------------------------------------------------------------------
// How a caveat is DRAWN — the corner badge, chosen by Griffin from
// tools/mark_sheet.py on 2026-08-01 ("I like the idea of the corner badge").
//
// This was a registry of three candidates until that pick; the losers are
// DELETED rather than left behind a flag, because three live code paths is how
// the practice card and the quick-select cell end up on different ones — the
// exact divergence one shared vocabulary exists to prevent. The sheet still
// regenerates, and adding a candidate back is adding an entry here.
//
// The badge's argument, in his terms: your RANK and a caveat about the time
// behind it are two different facts, so they are drawn in two places and
// neither hides the other. On the cell it takes the corner `.starrank-badge`
// already uses on the picker grid; on the card it can afford the word too.
export function cellBadge(caveat) {
  return html`<span class="caveat-badge" title=${caveat.sentence}
      aria-label=${caveat.sentence}>${caveat.glyph}</span>`;
}

// The card's badge is the cell's badge plus the WORD, which is the whole
// reason these two surfaces were designed together: the card can afford it and
// the cell cannot. The word is its own element so a narrow pane can drop it
// (index.html's 793px band, where the least load-bearing text gives way first)
// and leave the same glyph pill the cell wears -- the sentence stays reachable
// either way, because the title rides the CHIP rather than the word.
export function cardBadge(caveat) {
  return html`<span class="caveat-chip" title=${caveat.sentence}
      aria-label=${caveat.sentence}>${caveat.glyph}<span
      class="caveat-chip-word">${caveat.short}</span></span>`;
}

// ---------------------------------------------------------------------------
// Why the PB button is not there — a STRATEGY reason, which is a different
// kind of thing from a caveat and deliberately not in CAVEATS. A caveat says a
// saved time does not mean what the rank beside it implies; these say the row
// is perfectly fine and simply is not what you are practising right now.
//
// Keys mirror `tracking/pbaction.py::PB_GATE_REASONS` and are pinned equal by
// tests/test_cross_language_parity.py, for the same reason the caveat sets are:
// a key the server can send and this file cannot draw renders as nothing.
//
// The wording lives HERE and not on the server for the same reason the caveat
// sentences do — the server ships a key, the browser owns the words — and each
// is a function of the strategy name because one of the two prints it. The
// chip reads `<name> <tail>`: the two are split so the chip can clamp the NAME
// and always keep the word that carries the meaning. Strategy names run long
// -- a 100-coin star's are variant-qualified ("100c + Slide - Standard") --
// and a single string clamped to the column's width ellipsised away the
// "only", leaving a chip that just repeated a name (contact sheet, 2026-08-20).
export const PB_GATES = {
  foreign_strat: {
    // Reads as a fact about the button rather than an accusation: the run was
    // fine, it just belongs to another ladder. Retagging the row with its own
    // strategy picker is the way back in, which is the affordance already
    // sitting one cell to the left.
    name: (strat) => strat,
    tail: () => "only",
    sentence: (strat) =>
      `This run was not tagged ${strat}, and a PB is saved under the strategy `
      + `it was run with. Change the strategy on this row to save it.`,
  },
  no_active_strat: {
    name: () => null,               // no strategy to name -- that IS the state
    tail: () => "Pick a strategy",
    sentence: () =>
      "A PB is saved under one strategy, so pick the one you are practising "
      + "before saving.",
  },
};

// The server's own resolved `pb_blocked` ({reason, strat}) as the words a chip
// prints, or null for a caveat-shaped reason — that one is about the TIME, and
// the caller draws it as a disabled button wearing the caveat vocabulary.
export function pbGateOf(blocked) {
  if (!blocked) return null;
  const gate = PB_GATES[blocked.reason];
  if (!gate) return null;
  return { name: gate.name(blocked.strat), tail: gate.tail(blocked.strat),
           sentence: gate.sentence(blocked.strat) };
}

// The chip that takes the Save/Undo button's place when the row belongs to
// another strategy. The reason is PRINTED, never left on a hover: an
// explanation that only arrives on hover never arrives (his rule, 2026-08-02,
// about a disabled control whose own tooltip nobody reaches). Lives beside
// `cardBadge` so "change how the chip looks" is one function and one CSS block
// (`.pb-gate` in index.html), the same split the caveat badge has.
export function gateChip(gate) {
  if (!gate) return null;
  return html`<span class="pb-gate" title=${gate.sentence}
      aria-label=${gate.sentence}>${gate.name
        ? html`<span class="pb-gate-strat">${gate.name}</span>` : null
      }${gate.tail}</span>`;
}
