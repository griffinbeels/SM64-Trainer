// src/sm64_events/ui/components/targetpicker.js — the practice-target picker.
//
// Lived in header.js until 2026-07-26, opened by a PRACTICE TARGET context
// card. That card was removed: it named a target the Active-target card and
// the quick-select row already name, and its own pick was mostly unusable —
// you cannot practice Shifting Sand Land while loaded into Lethal Lava Land
// (user). The picker itself survives, re-homed onto the Active-target card,
// and an out-of-stage pick is now HELD as an intent by the server rather than
// applied (tracking/pending_target.py) — which is what makes picking a star
// somewhere else a sensible thing to do again.
//
// Exported as a hook returning [open, dialog] — the same shape as
// stagebanner.js's useIconPicking — so a caller supplies its own trigger and
// this module never has an opinion about what that trigger looks like.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { PickerDialog } from "./entitymodal.js";
import { StrategyStep } from "./strategystep.js";
import { courseUnionGroups, parseSegmentId, starId } from "../entities.js";
import { optionIconSrc } from "./entityicons.js";

const html = htm.bind(h);

export function useTargetPicker(t) {
  const v = t.view;
  const [editing, setEditing] = useState(false);
  // The picker's rank badges (courseUnionGroups' optional 4th arg). Keyed on
  // `editing` alone, never fetched on the host's other renders -- its host
  // re-renders on every WebSocket event while this dialog is normally closed.
  const [targetRanks, setTargetRanks] = useState({});
  useEffect(() => {
    if (!editing) return;
    let alive = true;
    // Clear the PREVIOUS open's badges before the new fetch lands, or
    // reopening paints stale ranks for a beat -- harmless flicker, but a cell
    // briefly wearing a rank it no longer has reads as a bug the moment the
    // real fetch corrects it (M9, final review 2026-07-26).
    setTargetRanks({});
    getJSON("/api/target/ranks")
      .then((ranks) => { if (alive) setTargetRanks(ranks); })
      .catch((fetchError) => console.error(fetchError));
    return () => { alive = false; };
  }, [editing]);

  useEffect(() => {
    if (!editing) return;
    const onKey = (event) => { if (event.key === "Escape") setEditing(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing]);

  // Re-picking the SAME star that's already the target, with a new (or first)
  // strategy, leaves the projector's target tuple unchanged -- no WebSocket
  // event in store.js's REFRESH_ON fires for that specific case
  // (set_target's truthy-strat_tag branch never publishes strat_set, unlike
  // set_target_segment's; verified empirically against TrackerService). Every
  // other /api/target call site (stagebanner.js, practice.js) refreshes
  // explicitly right after for exactly this reason -- this picker must too,
  // on every dismissal (a plain Esc/backdrop close just refetches data that
  // didn't change, which is harmless, and keeps one path rather than two).
  function close() { setEditing(false); t.refresh(); }

  // Only ever CALLED while the dialog is open (the `editing && v` guard below
  // short-circuits first) -- courseUnionGroups walks every course and
  // segment, and the host re-renders on every WebSocket event.
  function render() {
    // Art comes from entityicons.js, which builds the context itself — this
    // file never holds one. Hand-building a context per call site is what let
    // a segment cell fall through to a plain gold star while the banner drew
    // its real art (whole-branch review I1, 2026-07-25, fixed by supplying
    // the missing fields; unified into ONE builder 2026-07-26, after the Rank
    // tab turned out to have the same bug for a different missing field).
    // Layer 1 is a grid of COURSES carrying their portraits; layer 2 is that
    // course's stars AND the segments that begin in it, because both are
    // things you practice and /api/target already takes either (user,
    // 2026-07-25).
    const courseGroups = courseUnionGroups(
      v.catalog, t.segments || [], (t.vocab || {}).course_by_level || {},
      targetRanks,
    ).map((group) => ({
      ...group,
      icon: optionIconSrc(t, "course", group.key.replace("course-", "")),
    }));
    // The currently-set target, so the picker highlights it. `tgt` always
    // exists once `v` does (views.py always populates it with defaults).
    // Branching on `tgt.kind` FIRST is load-bearing: a SEGMENT target's
    // course_id is null, and computing starId unconditionally highlighted
    // Bob-omb Battlefield's first star instead of the segment being
    // practiced.
    const tgt = v.target;
    const targetValue = tgt.kind === "segment"
      ? `segment:${tgt.segment_id}`
      : tgt.course_id != null
        ? starId(Number(tgt.course_id), Number(tgt.star_id))
        : null;
    // placeholder=null renders no clear cell at either layer. /api/target
    // always requires an identity, so a clear cell posted {course_id: null,
    // star_id: null}, the server 409d, and the button silently did nothing
    // (dead by construction; whole-branch review, task 7, 2026-07-25).
    return html`<${PickerDialog} groups=${courseGroups} value=${targetValue}
      title="Choose a course" depth=${2} placeholder=${null}
      iconFor=${(id) => optionIconSrc(t,
        parseSegmentId(id) == null ? "star" : "segment",
        parseSegmentId(id) == null ? id : parseSegmentId(id))}
      nextStep=${StrategyStep}
      onPick=${close} onClose=${close} />`;
  }

  return [() => setEditing(true), editing && v ? render() : null];
}
