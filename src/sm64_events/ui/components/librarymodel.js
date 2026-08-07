// src/sm64_events/ui/components/librarymodel.js
//
// Pure rules for the Library page. Import-free ON PURPOSE: node drives these
// in tests/test_library_model_js.py without a browser, which is what pins
// section order, band order, the grid math and the trim mapping.

// Slowest -> fastest, Library-only vocabulary: the Library bands times
// against a fitted or vetted LADDER (library/ladders.py::fit_payload,
// ranks/standards.py), which never carries Iron -- Iron is the classify.py
// floor tier with no threshold of its own (ranks/classify.py's own docstring:
// "Iron is the implicit floor; it carries no threshold in data"), so there is
// no Iron cutoff to band against. DUPLICATED from ranks/classify.py::RANK_NAMES
// on purpose (importing classify's JS twin, caps.js, would drag Preact in --
// this module has to stay import-free) and held together by
// tests/test_cross_language_parity.py, not by this comment: it asserts RANKS
// equals RANK_NAMES reversed with Iron dropped, and GAME_FPS equals
// core/timefmt.py's own constant.
export const RANKS = ["Bronze", "Silver", "Gold", "Platinum", "Diamond",
                      "Master", "Grandmaster", "Mario"];
// Usamune's clock, matching core/timefmt.py::GAME_FPS -- trim seconds are
// GAME frames on THIS clock, not the source video's own frame rate
// (storage/db.py: "in/out_frame are non-destructive sync bounds in GAME
// frames"; tracking/comparisons.py::master_seek_time divides by the same 30
// default).
export const GAME_FPS = 30;

export function sectionOrder(approaches) {
  const keyed = approaches.map((a, i) => ({ a, i,
    mario: a.ladder && a.ladder.Mario != null ? a.ladder.Mario : -Infinity }));
  // beginner -> expert: slowest (most lenient) Mario cutoff first; no ladder
  // sinks last, keeping its published order (stable by original index).
  keyed.sort((x, y) => (y.mario - x.mario) || (x.i - y.i));
  return keyed.map((k) => k.a);
}

export function autoExpandName(ordered, selectedStrat) {
  if (!ordered.length) return null;
  if (selectedStrat) {
    const hit = ordered.find((a) => a.matched_strategy === selectedStrat
                                 || a.name === selectedStrat);
    if (hit) return hit.name;
  }
  return ordered[0].name;
}

export function bandFor(ladder, timeCs) {
  // A time earns the FASTEST tier whose cutoff it beats (<=, displayed cs --
  // the same rule ranks/classify.py::rank_for applies). rank_for walks
  // RANK_NAMES hardest-first and returns on the FIRST tier a time beats;
  // this walks RANKS easiest-first and keeps OVERWRITING on every tier a
  // time beats, so the final value is the LAST (= hardest) one that
  // matched -- the two are the same answer read in opposite directions
  // (proved for a non-monotonic ladder, not just an ordinary one, by
  // test_band_for_agrees_with_a_hand_walked_non_monotonic_ladder).
  let earned = null;
  for (const tier of RANKS) {
    if (ladder[tier] == null) continue;
    if (timeCs <= Math.round(ladder[tier] * 100)) earned = tier;
  }
  return earned || "Below Bronze";
}

export function bandsOf(ladder, entries) {
  const bands = [{ tier: "Below Bronze", cutoffCs: null, entries: [] }];
  for (const tier of RANKS) {
    if (ladder && ladder[tier] != null) {
      bands.push({ tier, cutoffCs: Math.round(ladder[tier] * 100), entries: [] });
    }
  }
  const byTier = Object.fromEntries(bands.map((b) => [b.tier, b]));
  for (const entry of entries) {
    byTier[ladder ? bandFor(ladder, entry.time_cs) : "Below Bronze"].entries.push(entry);
  }
  for (const band of bands) band.entries.sort((a, b) => b.time_cs - a.time_cs);
  return bands.filter((b) => b.entries.length || b.cutoffCs != null);
}

export function gridShape(n) {
  if (n <= 1) return { rows: 1, cols: 1 };
  if (n === 2) return { rows: 1, cols: 2 };
  if (n <= 4) return { rows: 2, cols: 2 };
  if (n <= 6) return { rows: 2, cols: 3 };
  return { rows: 3, cols: 3 };
}

export function youtubeId(url) {
  const m = (url || "").match(
    /(?:youtu\.be\/|youtube\.com\/(?:watch\?[^#]*v=|embed\/|shorts\/))([\w-]{6,})/);
  return m ? m[1] : null;
}
export function youtubeThumb(url) {
  const id = youtubeId(url);
  return id ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : null;
}
export function youtubeEmbed(url, startS) {
  const id = youtubeId(url);
  if (!id) return null;
  const m = (url || "").match(/[?&]t=(\d+)/);
  const start = Math.floor(startS != null ? startS : (m ? +m[1] : 0));
  return `https://www.youtube-nocookie.com/embed/${id}?start=${start}&enablejsapi=1`;
}

export function matchesRunner(entry, query) {
  return !query || entry.runner.toLowerCase().includes(query.toLowerCase());
}

export function lastPracticed(view) {
  // views.py::build_session_view ships `stars` and `segments` as two SEPARATE
  // top-level arrays -- never one merged `sections` list (ui/focustarget.js,
  // ui/components/compare.js and ui/components/practicelog.js already read
  // it this way). Compares `journal_id`, never the raw `id`: a reattributed
  // 100-coin attempt keeps a segment-namespace id (~7.5e11) that would
  // outrank every native id forever -- the exact bug
  // tracking-storage.md's "Attempt ordering must use journal_id" law and
  // focustarget.js::newestJournalId both already exist to avoid.
  let best = null, bestId = -1;
  const consider = (sec, key) => {
    for (const attempt of sec.attempts || []) {
      if (attempt.journal_id != null && attempt.journal_id > bestId) {
        bestId = attempt.journal_id;
        best = key;
      }
    }
  };
  for (const sec of (view && view.stars) || [])
    consider(sec, `star:${sec.course_id}:${sec.star_id}`);
  for (const sec of (view && view.segments) || [])
    consider(sec, `segment:${sec.segment_id}`);
  return best;
}

export function trayToImport(item, entityKey) {
  const trim = item.trim || {};
  const edit = (trim.start_s != null || trim.end_s != null) ? {
    in_frame: trim.start_s != null ? Math.round(trim.start_s * GAME_FPS) : null,
    out_frame: trim.end_s != null ? Math.round(trim.end_s * GAME_FPS) : null,
  } : null;
  // body matches server/compare_api.py::ImportBody's five fields exactly
  // (entity_key, strat, name, source_kind, source_ref) -- name is the
  // human-facing label the compare view shows, so it reads the time as
  // seconds ("Kally 43.80"), not the raw centisecond count.
  return { body: { entity_key: entityKey, strat: item.strat || "Standard",
                   name: `${item.runner} ${(item.time_cs / 100).toFixed(2)}`,
                   source_kind: "youtube", source_ref: item.video },
           edit };
}
