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
export const CAVEATS = {
  grab_timed: {
    glyph: "!",
    short: "Grab-timed",
    sentence: "Timed at the star grab, not the x-cam — not a leaderboard-legal time",
  },
  old_clock: {
    glyph: "≠",
    short: "Old clock",
    sentence: "Timed by wall-clock frames, not Usamune's IGT — not comparable to a fresh run",
  },
  unattributed: {
    glyph: "?",
    short: "Unattributed",
    sentence: "Not attributed to a strategy, so no rank can claim it (set a new PB to rank it)",
  },
};

// Severity, worst first. A row can legitimately carry more than one — a
// grab-timed star whose PB is also untagged — and one 16px slot can draw
// exactly one thing. The order is by what the mark CHANGES: a wrong quantity
// outranks an ungradeable one, because the reader can still act on a number
// that is merely unranked and cannot act on one that measures the wrong span.
export const CAVEAT_ORDER = ["grab_timed", "old_clock", "unattributed"];

export function worstCaveat(keys) {
  if (!keys || !keys.length) return null;
  return CAVEAT_ORDER.find((key) => keys.includes(key)) || null;
}

export function caveatOf(key) {
  return key ? CAVEATS[key] || null : null;
}

// ---------------------------------------------------------------------------
// Candidate TREATMENTS — three ways to draw the same fact, side by side.
//
// Shaped exactly like rankicon.js's ICON_STYLES on purpose: a registry of
// interchangeable renderers, judged against each other in one contact sheet
// (tools/mark_sheet.py) rather than one at a time, and narrowed to the picked
// one by DELETING the others. Every treatment answers all three slots, so a
// treatment that deliberately draws nothing somewhere returns null rather
// than being absent — that is what makes the sheet's grid comparable.
//
//   cellSlot     what replaces the rank medal in `.starrank` (16px, centred)
//   cellOverlay  what sits over the cell's art, out of flow
//   cellClass    a class on the cell root, for a treatment that RECOLOURS
//                something already there instead of adding an element
//   cardMark     what rides the practice card's PB tag
//
// `suppressFloor` is the one behavioural claim a treatment makes rather than a
// visual one: PracticeCell draws the ladder FLOOR when a rankable entity has
// no rank, and flooring an unattributed PB asserts a concrete rank that
// contradicts the PB sitting next to it — the exact live report ("Bowser 1
// shows PB 0'26"30, but the rank display clearly shows Capless 5... this
// should never happen") that `_section_banner`'s sentinel already fixed on the
// card. A treatment that puts its mark IN the rank slot suppresses the floor
// by construction; one that only decorates has to say so.
export const CAVEAT_TREATMENTS = {
  slot: {
    label: "In the rank slot",
    why: "The slot asks 'what rank is this'. When the answer is 'can't say', "
       + "say that instead of drawing a floor — no new geometry, no new element.",
    suppressFloor: true,
    cellSlot: (caveat) => html`<span class="caveat-slot" title=${caveat.sentence}
        aria-label=${caveat.sentence}>${caveat.glyph}</span>`,
    cellOverlay: () => null,
    cellClass: () => "",
    cardMark: (caveat) => html`<span class="caveat-inline" title=${caveat.sentence}
        aria-label=${caveat.sentence}>${caveat.glyph}</span>`,
  },
  badge: {
    label: "Corner badge",
    why: "Two different facts (your rank, and a caveat about the time behind it) "
       + "drawn in two places, so neither hides the other.",
    suppressFloor: true,
    cellSlot: () => null,
    cellOverlay: (caveat) => html`<span class="caveat-badge" title=${caveat.sentence}
        aria-label=${caveat.sentence}>${caveat.glyph}</span>`,
    cellClass: () => "",
    cardMark: (caveat) => html`<span class="caveat-chip" title=${caveat.sentence}
        aria-label=${caveat.sentence}>${caveat.glyph}${" "}${caveat.short}</span>`,
  },
  tinted: {
    label: "Tinted, no glyph",
    why: "The mark is a colour shift on the value itself rather than a symbol "
       + "beside it — nothing is added to a row that has no room to grow.",
    suppressFloor: false,
    cellSlot: () => null,
    cellOverlay: () => null,
    // The cell's only recolourable text. NOT the art: this repo has a ruling
    // against expressing state by dimming (the rank ladder's unreached bands,
    // 2026-07-27 -- "they all basically look black"), and a dimmed star reads
    // as disabled rather than as qualified.
    cellClass: () => "caveat-tinted",
    cardMark: (caveat) => html`<span class="caveat-word" title=${caveat.sentence}
        aria-label=${caveat.sentence}>${caveat.short}</span>`,
  },
};

export const DEFAULT_TREATMENT = "slot";

// The active treatment is a module-level slot for the same reason
// rankicon.js's icon style is: most of these call sites have no `t` in scope,
// and the contact sheet needs to render every treatment at once regardless of
// what is active. Nothing persists it — unlike the icon style this is NOT a
// user preference, it is a design decision waiting to be narrowed to one.
let active = DEFAULT_TREATMENT;

export function treatment(key = null) {
  return CAVEAT_TREATMENTS[key || active] || CAVEAT_TREATMENTS[DEFAULT_TREATMENT];
}

export function setCaveatTreatment(key) {
  if (CAVEAT_TREATMENTS[key]) active = key;
}
