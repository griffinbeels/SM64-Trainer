// src/sm64_events/ui/components/emptystate.js — the "nothing here yet" slot.
//
// Four panels on the Practice tab can legitimately have nothing to draw
// (attempt timeline, performance trend, practice log, and the whole analysis
// card when no target is picked). Before this, each one rendered an empty
// box: Progress returns "" with no plottable points, AttemptTable renders a
// <table> with no rows, and a card with a fixed 458px height turned that into
// a large silent void that reads as a broken render rather than an honest
// state (live report 2026-07-25, with the reference: "like a 404 message").
//
// So it is built like a good 404 — one illustration, one headline saying what
// is missing, one line saying how to fill it, and a joke that costs the user
// nothing to skip. The art is a random member of the cast below, dimmed hard
// (the CSS carries the opacity) so it decorates the panel instead of
// competing with the live data in the cards around it.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

// The cast. One entry per PNG in ui/assets/empty/ — coverage is pinned BOTH
// ways by tests/test_ui_empty_states.py, so a renamed file fails the suite
// instead of 404ing a broken-image icon into the card, and an added PNG that
// nobody wired up fails too. Quips are asides, never instructions: the
// headline and hint carry every word the user actually needs.
export const CAST = [
  { stem: "boo_shy", name: "Boo",
    quip: "Boo can't bear to look at an empty log." },
  { stem: "boo_normal", name: "Boo",
    quip: "Boo ate the data. Boo is not sorry." },
  { stem: "ukiki_1", name: "Ukiki",
    quip: "Ukiki swears he didn't touch anything." },
  { stem: "ukiki_2", name: "Ukiki",
    quip: "Ukiki took your hat AND your splits." },
  { stem: "toad", name: "Toad",
    quip: "Toad has nothing to report, enthusiastically." },
  { stem: "toad_2", name: "Toad",
    quip: "Toad checked twice. Still empty." },
];

// Module-level memory of the last character handed out, so the two empty
// states that can be on screen together (the analysis card and the practice
// log) never draw the same one — twins read as a rendering bug, which is the
// exact impression this component exists to remove. Deliberately NOT a
// shared context or a store field: the only thing any caller needs is "not
// the one my neighbour just took", and mount order gives that for free.
//
// Excluded by NAME, not by file: two of the six PNGs are Ukiki and two are
// Toad, and a first render that drew ukiki_1 beside ukiki_2 still read as the
// same monkey twice (headless render 2026-07-25).
let lastName = null;

export function pickCast() {
  const pool = CAST.filter((c) => c.name !== lastName);
  const cast = pool[Math.floor(Math.random() * pool.length)];
  lastName = cast.name;
  return cast;
}

export function castSrc(stem) {
  return `/ui/assets/empty/${stem}.png`;
}

// headline: what is missing, in the panel's own terms.
// hint:     the one action that fills it.
// The character is drawn once per MOUNT (useState's lazy initializer), never
// per render — the view refetches on every WebSocket event, and re-rolling
// there would flicker a new character through the panel several times a
// minute while the user is mid-run.
export function EmptyState({ headline, hint }) {
  const [cast] = useState(pickCast);
  return html`<div class="empty-state">
    <div class="empty-art">
      <img src=${castSrc(cast.stem)} alt="" aria-hidden="true"
           onerror=${(e) => { e.target.style.visibility = "hidden"; }} />
    </div>
    <b class="empty-headline">${headline}</b>
    <span class="empty-hint">${hint}</span>
    <span class="empty-quip">${cast.quip}</span>
  </div>`;
}
