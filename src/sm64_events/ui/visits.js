// src/sm64_events/ui/visits.js
//
// Cutting the recorder's list of moments into one card per PLACE YOU WERE IN.
//
// Griffin, 2026-08-05: "we should segment each of the events by the course /
// area that the event occurred in… Each high level area should be its own card
// (e.g., if I move between HMC and LLL, both HMC and LLL get their own cards).
// Within each contains all of the events that occurred during that area. That
// way, when we observe the live viewer, we can easily and quickly identify
// which areas contained which events."
//
// WHERE each row happened is decided on the SERVER (`GET /api/segments/
// timeline` stamps `place`/`place_label`/`place_level`): most journal rows do
// not say where they are — a reset names nothing at all — so position is a
// running total over the whole journal, and the browser holds only a windowed
// tail with no beginning to walk from. This module only groups what arrives.
//
// Import-free, so tests/test_ui_recorder_visits.py drives the REAL rule under
// node. A Python reimplementation would be a second copy of the one thing that
// decides where a card breaks.

/**
 * `rows` NEWEST FIRST (the order the recorder draws), each carrying `place`,
 * `place_label` and `place_level`. Returns one card per consecutive run of the
 * same place: `{key, place, label, level, rows}`.
 *
 * ONE CARD PER VISIT, never one per place. HMC → LLL → HMC is three cards, not
 * two: this is a timeline, and folding the two HMC visits together would put
 * events minutes apart under one heading with nothing saying they were
 * separate trips.
 *
 * THE KEY IS THE CARD'S OLDEST ROW, and that is the load-bearing part. New
 * rows land at the NEWEST end (the live tail fetch appends to the journal
 * order, which is the top of this list), so they extend the newest card or
 * start a fresh one — either way no existing card's oldest row moves. Keying
 * on the newest row instead re-keys a card every time it grows, and a re-keyed
 * card is one whose collapsed state resets underneath the user mid-play.
 *
 * A row with no place (the first frames of a fresh journal, before any area is
 * established) groups with its neighbours that also have none — `null` is a
 * value here, not a hole to skip. The caller names it.
 */
export function visitCards(rows) {
  const cards = [];
  for (const row of rows || []) {
    const last = cards[cards.length - 1];
    if (last && last.place === row.place) last.rows.push(row);
    else cards.push({ place: row.place, label: row.place_label,
                      level: row.place_level, rows: [row] });
  }
  return cards.map((card) => ({
    ...card, key: String(card.rows[card.rows.length - 1].id) }));
}
