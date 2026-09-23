// src/sm64_events/ui/pbcrown.js — which practice-log rows were JUST crowned a
// personal best, read off the live event feed.
//
// Task 0147 (2026-09-23): the automatic PB took the player's own Save click
// away, and that click was where he felt the PB. His ask: "the point is to
// really really really make it feel special when the user gets a gold... super
// obvious that THAT specific practice log entry was the personal best
// (especially because we've automated it)". The row that IS the PB carries a
// standing mark (attemptlog.js + index.html's "Personal best" block); the row
// named here plays the one-shot arrival on top of it.
//
// The trigger is the `pb_saved` BROADCAST -- automatic or a Save click -- and
// never a diff of the view. A strategy switch, a clock flip, a scope change and
// a page load all move which row is the PB without anyone setting one, and a
// celebration fired by those replays an obsolete win. `pb_undone` crowns
// nothing: undoing is not an achievement, so the restored PB lights up through
// the same quiet transition that drains the undone row.
//
// Import-free, so node can drive it directly.

// How long a crowned id stays crowned. The broadcast lands BEFORE the view
// refetch that paints the row as the PB, so the row must still find its id
// when that paint arrives; a closed card opening inside the window plays it
// too, which is the same PB he just set.
export const CROWN_MS = 6000;

/**
 * The attempt ids a `pb_saved` crowned since the last read, oldest first.
 * `feed` is store.js's newest-first event list; `seen` is a WeakSet of events
 * already read, which this marks as it goes. The feed only ever grows at the
 * front, so the first event already seen means everything older was too.
 */
export function crownedSince(feed, seen) {
  const ids = [];
  for (const event of feed || []) {
    if (seen.has(event)) break;
    seen.add(event);
    if (event && event.type === "pb_saved" && event.payload
        && event.payload.attempt_id != null) ids.push(event.payload.attempt_id);
  }
  return ids.reverse();
}
