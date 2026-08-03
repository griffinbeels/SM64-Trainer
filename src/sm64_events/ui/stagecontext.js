// src/sm64_events/ui/stagecontext.js
// "Is there anything to practice where the player is standing?"
//
// TWO surfaces ask it and they must never disagree: the quick-select banner
// (a row per context, else "No course target available") and the Active-target
// card (what you are practicing, else "no active objective"). They answered it
// separately until 2026-07-27, and diverged exactly where it shows — a new
// session on the game's main screen drew the placeholder AND, right underneath
// it, the previous session's Lethal Lava Land star wearing an ACTIVE TARGET
// eyebrow. So the banner calls this to choose its placeholder and practice.js
// calls it to suppress the pinned card: one door, by construction rather than
// by agreement.
//
// This is a question about the PLACE, never about the target. The target
// itself legitimately survives a hub — every course is entered through the
// castle, so retiring one there would drop it a room before it could be
// practiced (projection.py caveat 12) — it simply isn't ACTIVE while you are
// standing somewhere it cannot be run. Walk back in and the same target is
// there. The server half of the same rule is tracking/practicable.py, which
// refuses a pick from a place like this for the same reason.
//
// Import-free on purpose: node drives it directly (tests/test_ui_practice_
// context.py), and both consumers import it rather than re-deriving it.

// The stage modes detectors/stage.py emits that the banner has a row for.
// `null` is not on the list and is not an unknown either: the file select, the
// castle grounds, the courtyard and the cap courses are all real places that
// offer nothing to practice. One entry per key of stagebanner.js's STAGE_ROWS,
// pinned both ways by tests/test_ui_practice_context.py.
export const PRACTICE_MODES = ["stars", "bowser_course", "arena", "castle"];

// The segments whose timer is live right now, in this view's vocabulary.
export const armedSegments = (t, view) =>
  (view.segment_targets || []).filter((s) => t.armedSegs.has(s.segment_id));

// Does a section still BELONG where the player is standing?
//
// The second question, and a looser one than the first: setting a target
// needs you standing exactly at its node (tracking/practicable.py), but
// STAYING on one only needs you not to have walked into a different course.
// Every course is entered through the castle and the hubs, so those are
// transit and drop nothing — which is what keeps a card up while you walk
// back to a movement's start, and keeps the star you just practiced on screen
// in the lobby. Exactly the projector's own retirement rule (caveat 12),
// asked about a PLACE rather than about a transition.
//
// Both kinds carry `course_id` for it (rule 11): a star's course, a segment's
// course via `origin_course(segment_origin(...))` in views.py, null for the
// castle interior. It is a STATIC fact about the entity, deliberately — a
// server-computed "is it here" boolean could not be frozen, so a celebration
// that ran while the player walked out would drop the card it was celebrating.
// The freeze lives on `stage` (practice.js's `held`), so pass the same `t`.
//
// The bug this exists for: `lastPinnedSeg` is a sticky client-side memory,
// set on arm and never cleared by a place change, so a lobby LBLJ stayed
// "ACTIVE SEGMENT" after a Usamune warp into Whomp's Fortress and then Hazy
// Maze Cave. The server had already retired the TARGET both times — the card
// was being held up by the pin alone, which is why it read "Recent".
// TIGHTENED 2026-08-03, and the loosest clause was the bug: standing anywhere
// with no course of its own — the castle, a hub, an arena — used to return TRUE
// for everything, so a card could name a thing belonging to a course two rooms
// away. Live report, standing in Castle Upstairs with only `BitS Entry` in the
// selector: *"the 'active' card section (right now it says Bowser in the Fire
// Sea) should never display anything other than any option that's currently
// displayed in the segment / star selector above… if we leave a bowser stage,
// we're no longer in the bowser stage, so it shouldn't be displayed as active."*
//
// "Transit" is still right about what it was written for — walking through the
// castle must not drop a CASTLE MOVEMENT, which is how a card stays up while
// you walk to its start. It was never right about a thing that lives in a
// course you have left. So a place with no course keeps only things that also
// have no course; a course keeps its own, as before.
//
// The ARMED exemption is unchanged and lives at the call site (`sec
// .armed_detail || here(sec)` in practice.js): a running movement is visible
// wherever it got to, and the selector agrees — every row appends
// `armedExtraCells` for exactly that. So "the card shows only what the selector
// shows" holds in both directions rather than by coincidence.
export function practicedHere(section, t) {
  if (!t.stage) return true;               // no live stage: unknown, allow
  const standingIn = t.stage.course_id;
  if (section == null) return false;
  if (standingIn == null)                  // castle, hub, arena
    return section.course_id == null;
  return section.course_id === standingIn;
}

// Where the player is standing, as a mode id or null. Exported so that no
// other file has to reach for `stage.mode` itself — reading it is how a second
// surface starts deciding for itself what counts as practicing, which is the
// divergence above (tests/test_single_source.py owns that rule).
export const practiceMode = (t) => (t.stage && t.stage.mode) || null;

// WHICH selector surface is on screen — the empty state, a course's stars, a
// Bowser row, an arena, one of the castle's three areas. The exchange in
// stagebanner.js fades between two of these (live report 2026-08-02: "if there
// previously were no options available, but I transition to a stage with
// options, I would expect the animation to happen"), and it lives here for the
// same reason `practiceMode` does: this is a judgement about the PLACE, and a
// second file deciding what counts as a different place is how the two surfaces
// came apart in the first place.
//
// It is deliberately COARSER than "the cells changed" — that question belongs to
// the cells themselves (components/cellrow.js). The line between them is drawn
// where the player would agree: the castle's three areas are three different
// sets of movements, while a course's own subareas are the same seven stars, so
// entering SSL's pyramid must not blink a row that is not changing.
export function selectorSurfaceId(t) {
  if (!hasPracticeContext(t)) return "nothing-here";
  const stage = t.stage || {};
  const mode = practiceMode(t);
  return mode === "castle" ? `castle:${stage.area}`
                           : `${mode}:${stage.level}`;
}

// Can a STAR be practiced where the player is standing? Narrower than
// hasPracticeContext on purpose, and the distinction is the whole point.
//
// `hasPracticeContext` answers "is there anything to practice here at all",
// and its last clause is `armedSegments(...).length > 0` — a RUNNING segment
// is its own context (user rule 2026-07-24), which is what keeps a castle
// movement visible on the grounds. That clause is about SEGMENTS. Reusing it
// to gate the star card meant an unrelated armed segment kept a star target
// rendering as ACTIVE TARGET somewhere the star cannot be run: the user
// exited Whomp's Fortress to the Castle Lobby and the card still read
// `ACTIVE TARGET · Whomp's Fortress · 100 Coins`, on the same screen as the
// banner's own "No course target available" (live report 2026-07-30).
//
// `practicedHere` does not catch it either, and also by design: a hub returns
// true there because the castle is TRANSIT — a target legitimately survives
// walking through it and is restored on the way back in. Surviving is right;
// rendering as ACTIVE while you stand somewhere it cannot be run is not.
//
// So the star card asks about the PLACE — a stage whose mode actually offers
// stars — and nothing else. The target itself is untouched.
export const starPracticableHere = (t) =>
  PRACTICE_MODES.includes(practiceMode(t));

export function hasPracticeContext(t) {
  const view = t.view;
  if (!view) return false;
  if (PRACTICE_MODES.includes(practiceMode(t))) return true;
  // A RUNNING segment is never invisible (user rule 2026-07-24). It is being
  // practiced wherever it has got to by now, so it IS its own context — this
  // is the clause that keeps a castle movement armed on the grounds both on
  // screen and pinned.
  return armedSegments(t, view).length > 0;
}

// "Just completed" a segment (practice.js's pinned-card gate, spec
// 2026-07-28-multi-step-segments): true only when the segment's own most-
// recent attempt (by id — a section's attempts are not guaranteed newest-
// first) landed as a FRESH success. `freshIds` is practice.js's own
// attempt-id recency Set (useFreshAttemptIds). Shared here rather than
// defined per call site, so a segment that arms AMBIENTLY (arms_ambiently)
// but was just deliberately completed still gets its brief "just finished"
// grace period instead of vanishing from the pin the instant it disarms.
export const justCompletedSegment = (v, freshIds, segmentId) => {
  if (!freshIds || !freshIds.size) return false;
  const sec = (v.segments || []).find((s) => s.segment_id === segmentId);
  if (!sec || !sec.attempts.length) return false;
  const latest = sec.attempts.reduce((a, b) => (a.id > b.id ? a : b));
  return latest.outcome === "success" && freshIds.has(latest.id);
};

// "Just completed" a STAR -- the same question as justCompletedSegment, one
// level up (spec 2026-07-28-multi-step-segments round 2, item 2: the
// Bowser Reds row's detection-driven family memory). True only when the
// star's own most-recent attempt landed as a FRESH success. Mirrors
// justCompletedSegment exactly; kept as a sibling function rather than a
// shared generic (a star section is keyed by course_id/star_id, a segment
// section by segment_id -- two different lookups, one small body each) so
// neither call site has to build a fake identity to satisfy the other's
// shape.
export const justCompletedStar = (v, freshIds, courseId, starId) => {
  if (!freshIds || !freshIds.size) return false;
  const sec = (v.stars || [])
    .find((s) => s.course_id === courseId && s.star_id === starId);
  if (!sec || !sec.attempts.length) return false;
  const latest = sec.attempts.reduce((a, b) => (a.id > b.id ? a : b));
  return latest.outcome === "success" && freshIds.has(latest.id);
};

// Does a ladder EXIST for this entity, whatever this player has run on it?
//
// `rank` is null both when nothing can be graded and when the player simply has
// no time yet, and those must draw differently: with standards, an unranked
// entity shows the ladder FLOOR (caps.js's RANK_FLOOR — Capless 5 today) so it
// reads as "bottom of the ladder"; without them it still shows nothing, because
// there is no ladder for a floor to be the bottom of. User, 2026-07-30: "for
// every star/segment that we don't have a rank for, but there exist rank
// standards for, instead of displaying a '-' we should display the Capless 5
// icon in its place".
//
// Memoised on the view's own array identity — every cell in a row asks this,
// the view is a fresh object per fetch, and rebuilding a ~200-entry Set per
// cell per render is the kind of cost that only shows up on the Rank tab's
// coverage strip.
let _standardsSet = null;
let _standardsSrc = null;
export const hasStandardsFor = (view, entityKey) => {
  const eks = (view || {}).standards_eks;
  if (!eks || !eks.length || !entityKey) return false;
  if (_standardsSrc !== eks) { _standardsSrc = eks; _standardsSet = new Set(eks); }
  return _standardsSet.has(entityKey);
};
