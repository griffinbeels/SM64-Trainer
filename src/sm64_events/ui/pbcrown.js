// src/sm64_events/ui/pbcrown.js — what the live event feed says about PBs the
// practice log is showing: which rows were JUST crowned, and how much time
// each of those crownings saved.
//
// Task 0147 (2026-09-23): the automatic PB took the player's own Save click
// away, and that click was where he felt the PB. His ask: "the point is to
// really really really make it feel special when the user gets a gold... super
// obvious that THAT specific practice log entry was the personal best
// (especially because we've automated it)". The row that IS the PB carries a
// standing mark (attemptlog.js + index.html's "The PB row" block); the rows
// named here play the one-shot arrival on top of it.
//
// The trigger is the `pb_saved` BROADCAST -- automatic or a Save click -- and
// never a diff of the view. A strategy switch, a clock flip, a scope change and
// a page load all move which row is the PB without anyone setting one, and a
// celebration fired by those replays an obsolete win. `pb_undone` crowns
// nothing: undoing is not an achievement, so the restored PB lights up through
// the same quiet transition that drains the undone row.
//
// THE TIME SAVE (same day, his words): "Time Save: -X.XX (Y frames)... It
// should persist until the next attempt is logged (a reset, a successful
// attempt, etc). Once there's another attempt, we should animate that text out
// and get rid of it, leaving just "GOLD!!"... It doesn't really matter anymore
// after the first time you see it anyway". So a save's margin lives from its
// `pb_saved` (which names the PB it replaced, `previous_frames`) until the next
// `attempt_completed` -- every outcome closes through that one event -- and an
// Undo takes its own margin with it. A first PB replaced nothing and saved
// nothing, so it has none.
//
// Import-free, so node can drive it directly.

// How long a crowned id stays crowned. The broadcast lands BEFORE the view
// refetch that paints the row as the PB, so the row must still find its id
// when that paint arrives; a closed card opening inside the window plays it
// too, which is the same PB he just set.
export const CROWN_MS = 6000;

/**
 * Fold the events not yet read into the standing time saves. `feed` is
 * store.js's newest-first event list; `seen` is a WeakSet of events already
 * read, which this marks as it goes (the feed only grows at the front, so the
 * first event already seen means everything older was too). `timeSaves` maps
 * attempt id -> frames saved and is never mutated.
 *
 * Returns `{ crowned, timeSaves }`: the ids a `pb_saved` crowned, oldest
 * first, and the time saves standing after those events, in their order --
 * an attempt logged BEFORE a save (the run an automatic PB saves) leaves that
 * save standing; one logged after it clears it.
 */
export function readPbNews(feed, seen, timeSaves) {
  const unread = [];
  for (const event of feed || []) {
    if (seen.has(event)) break;
    seen.add(event);
    unread.push(event);
  }
  return unread.reverse().reduce(foldPbEvent, { crowned: [], timeSaves });
}

// One event's effect, oldest first. Returns a new state whenever the saves
// change, so a caller can tell "nothing happened" by identity.
function foldPbEvent(state, event) {
  const payload = (event && event.payload) || {};
  const id = payload.attempt_id;
  if (event.type === "attempt_completed") {
    return state.timeSaves.size ? { ...state, timeSaves: new Map() } : state;
  }
  if (event.type === "pb_saved" && id != null) {
    const saved = (payload.previous_frames ?? -Infinity) - (payload.frames ?? Infinity);
    const timeSaves = saved > 0 ? new Map(state.timeSaves).set(id, saved) : state.timeSaves;
    return { crowned: [...state.crowned, id], timeSaves };
  }
  if (event.type === "pb_undone" && state.timeSaves.has(id)) {
    const timeSaves = new Map(state.timeSaves);
    timeSaves.delete(id);
    return { ...state, timeSaves };
  }
  return state;
}

/** "Time Save: -0.30 (9 frames)" -- his wording, 30 fps like every delta. */
export function timeSaveText(frames) {
  return `Time Save: -${(frames / 30).toFixed(2)} (${frames} ${frames === 1 ? "frame" : "frames"})`;
}
