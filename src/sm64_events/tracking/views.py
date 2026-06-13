"""Builds the GET /api/session payload.

Contract (the UI builds against ALL of this):
- `scope` selects which attempts drive sections/attempt lists/unassigned:
  "session" (default) = the active session, "lifetime" = everything.
  Stat chips and the timeline ALWAYS compute over lifetime history (spec §8).
- Star sections are ordered newest-activity-first (max scoped journal
  recency via projection.journal_id; fresh targets sort last); segment
  sections order among themselves the same way.
- The practice target's section is ALWAYS present, even with zero scoped
  attempts — the UI pins it as the active block (star AND segment kinds).
  ARMED segments are pinned the same way: active now => section present.
- Sections carry `markers_by_strat` (spec §3) and `progress` (spec §4,
  scoped successes grouped per session).
- Segment sections (`segments` key) mirror star sections but are RTA-only
  (segments have no IGT): pb / attempts / stats / timeline / progress all
  read rta_frames whatever the view clock. Marker keys: 'seg:<id>:<strat>'.
- `target` is kind-aware: service.target_payload() identity + display
  names, every key present for both kinds (shape stability)."""
from sm64_events.core.timefmt import format_igt
from sm64_events.links import star_links
from sm64_events.memory.addresses import (COURSE_NAMES, course_name,
                                          star_count, star_name)
from sm64_events.stats.registry import (DEFAULT_STAT_MENU, REGISTRY,
                                        compute_stat, selection_id,
                                        selection_order)
from sm64_events.tracking.projection import journal_id

# Timeline markers (per-section event graph): outcomes that plot as points.
# Adding a marker kind is one row here (+ a style row in ui timeline.js).
# The frame position comes from the section's clock extractor: star
# sections pass igt (resets/deaths only have an IGT position), segment
# sections pass rta (segments have no IGT).
TIMELINE_OUTCOMES = frozenset({"success", "reset", "death"})


def _timeline(history, frames_of) -> dict | None:
    """X axis 0 -> longest SUCCESSFUL attempt; every qualifying attempt is
    a point at its frames_of(a) position. Points may exceed max_frames (a
    reset later than the best success) — the UI extends the axis as needed.

    The axis ends at the longest success when one exists, otherwise at the
    rightmost point; max_is_success=False lets the UI render a provisional
    axis until a success lands. Each point's display string keeps the "igt"
    key whatever the clock (UI contract — it is just formatted frames)."""
    points = []
    for a in history:
        if a.cleared or a.outcome not in TIMELINE_OUTCOMES:
            continue
        frames = frames_of(a)
        if frames is None:
            continue
        points.append({"frames": frames, "igt": format_igt(frames),
                       "outcome": a.outcome, "attempt_id": a.id})
    if not points:
        return None
    succ = [p["frames"] for p in points if p["outcome"] == "success"]
    max_frames = max(succ) if succ else max(p["frames"] for p in points)
    return {"max_frames": max_frames, "max_display": format_igt(max_frames),
            "max_is_success": bool(succ), "points": points}


def _fmt(value, fmt):
    if value is None:
        return None
    if fmt == "time":
        return format_igt(round(value))
    if fmt == "percent":
        return f"{round(value * 100)}%"
    return str(value)


def _current_pbs(pb_rows: list[dict]) -> dict:
    """Latest pb row per kind-aware key: ("segment", segment_id, mode) for
    segment rows, (course_id, star_id, mode) for star rows. Without the
    kind tag every segment pb collapses onto (None, None, "rta") and the
    newest segment's save shadows all the others (live bug, Task 12)."""
    out = {}
    for row in pb_rows:  # ordered by id: later rows win
        key = (("segment", row["segment_id"], row["timer_mode"])
               if row["segment_id"] is not None
               else (row["course_id"], row["star_id"], row["timer_mode"]))
        out[key] = row
    return out


def _attempt_json(a, pbs, clock):
    pb = pbs.get(("segment", a.segment_id, clock) if a.segment_id is not None
                 else (a.course_id, a.star_id, clock))
    frames = a.igt_frames if clock == "igt" else a.rta_frames
    race_row = clock == "rta" and frames == 0  # same-tick reset-race: rta is junk (see projection.py docstring)
    delta = (frames - pb["frames"]
             if pb and frames is not None and not race_row and a.outcome == "success"
             else None)
    return {"id": a.id, "outcome": a.outcome, "outcome_detail": a.outcome_detail,
            "anchor_type": a.anchor_type, "strat_tag": a.strat_tag,
            "igt_frames": a.igt_frames,
            "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
            "rta_frames": a.rta_frames,
            "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
            "pb_delta_frames": delta, "cleared": a.cleared,
            # this attempt owns the CURRENT pb row on this clock — drives
            # the Save-as-PB / Undo-PB button swap (undo deletes that row)
            "is_current_pb": bool(pb) and pb["attempt_id"] == a.id,
            "cleared_reason": a.cleared_reason,
            "started_utc": a.started_utc, "ended_utc": a.ended_utc,
            "rollouts_total": a.rollouts_total,
            "rollouts_dustless": a.rollouts_dustless,
            "jumps_total": a.jumps_total,
            "jumps_dustless": a.jumps_dustless,
            "segment_id": a.segment_id}


def _catalog() -> dict:
    courses = []
    for cid, cname in COURSE_NAMES.items():
        # max(..., 1): the catalog always shows at least one star row even
        # for course 0 (display fallback); the count itself lives in
        # addresses.star_count
        n = max(star_count(cid), 1)
        courses.append({"id": cid, "name": cname,
                        "stars": [star_name(cid, s) for s in range(n)]})
    return {"courses": courses}


_CATALOG = _catalog()


def _strategies_for(registered: dict, attempts, course_id: int, star_id: int) -> list[str]:
    """Registered strategies (ui_state) merged with every strat ever used
    on this star's attempts — union preserves registration order first."""
    out = list(registered.get(f"{course_id}:{star_id}", []))
    for a in attempts:
        if (a.course_id, a.star_id) == (course_id, star_id) \
                and a.strat_tag and a.strat_tag not in out:
            out.append(a.strat_tag)
    return out


def _markers_for(markers_state: dict, course_id, star_id) -> dict:
    """strat -> sorted marker list for ONE section, from the ui_state KV.
    Key shape '<course>:<star>:<strat>' for stars, 'seg:<id>:<strat>' for
    segment sections (call with ("seg", segment_id)); '' = no strategy."""
    prefix = f"{course_id}:{star_id}:"
    return {k[len(prefix):]: v for k, v in markers_state.items()
            if k.startswith(prefix)}


def _stats_for(history, stat_menu, clock) -> list[dict]:
    """Stat chips for one section: canonical registry order, deduped by
    selection identity, computed over the LIFETIME history (spec §8).
    Star sections pass the view clock; segment sections always pass "rta"."""
    stats = []
    seen_stat_ids: set[str] = set()
    for sel in sorted(stat_menu,
                      key=lambda s: selection_order(s.get("key", ""),
                                                    s.get("params"))):
        if sel["key"] not in REGISTRY:
            continue
        sid = selection_id(sel["key"], sel.get("params"))
        if sid in seen_stat_ids:
            continue
        seen_stat_ids.add(sid)
        d = REGISTRY[sel["key"]]
        try:
            value = compute_stat(sel["key"], history, sel.get("params"), clock)
        except (ValueError, TypeError, KeyError):
            value = None  # bad stored params (e.g. n="abc") must not 500 the view
        # label N-substitution is keyed to avg_last_n; a future parameterized stat needs a label_template field instead
        label = d.label.replace("N", str(sel.get("params", {}).get("n", ""))) \
            if d.key == "avg_last_n" else d.label
        stats.append({"key": d.key, "label": label,
                      "params": sel.get("params", {}), "fmt": d.fmt,
                      "value": value, "display": _fmt(value, d.fmt)})
    return stats


def _progress(attempts, pb_ids: set, session_meta, frames_of) -> dict | None:
    """Completion-time-over-time points (spec §4): non-cleared successes of
    the SCOPED attempt list, grouped by session, chronological. A success
    qualifies when the section's clock (frames_of: stars igt, segments rta)
    has a value; every point still ships BOTH clock fields (the UI picks).
    Gold = explicitly saved PB rows (every save stays gold even when
    superseded). rta race rows (rta_frames == 0) ship as-is; the UI filters
    them. Resumed sessions append to their original group; within-group id
    order is still chronological (journal ids are wall-clock monotonic)."""
    by_session: dict[int, list] = {}
    for a in attempts:
        if a.outcome != "success" or a.cleared or frames_of(a) is None:
            continue
        by_session.setdefault(a.session_id, []).append({
            "t_utc": a.ended_utc,
            "igt_frames": a.igt_frames,
            "rta_frames": a.rta_frames,
            "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
            "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
            "attempt_id": a.id,
            "is_pb_igt": (a.id, "igt") in pb_ids,
            "is_pb_rta": (a.id, "rta") in pb_ids,
        })
    if not by_session:
        return None
    return {"sessions": [
        {"session_id": sid,
         "label": session_meta.get(sid, {}).get("label"),
         "started_utc": session_meta.get(sid, {}).get("started_utc"),
         "points": pts}
        for sid, pts in sorted(by_session.items())]}


def build_session_view(db, service, clock: str, scope: str = "session") -> dict:
    all_attempts = db.attempts()
    session_attempts = [a for a in all_attempts
                        if a.session_id == service.session_id]
    # scoped determines which attempts drive the seen-set, in_section lists,
    # and unassigned list. Stats always use lifetime (all_attempts).
    scoped = all_attempts if scope == "lifetime" else session_attempts
    pb_rows = db.pbs()
    pbs = _current_pbs(pb_rows)
    pb_ids = {(r["attempt_id"], r["timer_mode"]) for r in pb_rows}
    sessions_list = db.sessions()
    session_meta = {s["id"]: s for s in sessions_list}
    stat_menu = db.get_state("stat_menu", default=DEFAULT_STAT_MENU)
    registered = db.get_state("strategies", {})
    markers_state = db.get_state("timeline_markers", {})

    sections, unassigned = [], []
    seen: dict[tuple[int, int], None] = {}
    seen_segs: dict[int, None] = {}
    # newest-activity recency per section key; scoped is journal-id-ordered
    # WITHIN each key, so the last write per key wins. journal_id() strips
    # the segment-id namespace offset so star and segment sections both
    # compare by underlying journal recency.
    last_id: dict = {}
    for a in scoped:
        if a.segment_id is not None:   # segment attempts have course_id None
            seen_segs[a.segment_id] = None  # ...but are NEVER unassigned
            last_id[("segment", a.segment_id)] = journal_id(a.id)
        elif a.course_id is None:
            unassigned.append(_attempt_json(a, pbs, clock))
        else:
            seen[(a.course_id, a.star_id)] = None
            last_id[(a.course_id, a.star_id)] = journal_id(a.id)

    # the practice target ALWAYS gets a section (spec §5), whichever kind:
    # setting a target immediately surfaces its lifetime history, PB, and
    # markers. Fresh targets have no recency entry (-1) and sort last.
    if service.target and service.target[0] == "star" \
            and service.target[1:] not in seen:
        seen[service.target[1:]] = None
    if service.target and service.target[0] == "segment":
        seen_segs.setdefault(service.target[1], None)
    # armed segments are "active now" by the same philosophy as the target
    # pin: their sections render even with zero attempts, so the armed
    # badge has somewhere to live and a plain refresh self-heals it.
    # sorted = deterministic tie order among fresh (-1 recency) sections.
    armed = service.armed_segment_ids
    for sid in sorted(armed):
        seen_segs.setdefault(sid, None)

    scoped_set = set(scoped)
    igt_of = lambda a: a.igt_frames
    for course_id, star_id in seen:
        history = [a for a in all_attempts
                   if a.course_id == course_id and a.star_id == star_id]
        in_section = [a for a in history if a in scoped_set]
        pb_json = {}
        for mode in ("igt", "rta"):
            row = pbs.get((course_id, star_id, mode))
            pb_json[mode] = ({"frames": row["frames"],
                              "display": format_igt(row["frames"])}
                             if row else None)
        # Note: star sections intentionally omit "kind". The UI branches on
        # sec.kind being undefined for stars (SegmentSection vs StarSection),
        # so adding kind="star" here would silently break that check. Do not
        # add the key unless the UI branch is updated at the same time.
        sections.append({
            "course_id": course_id, "star_id": star_id,
            "course_name": course_name(course_id),
            "star_name": star_name(course_id, star_id),
            "links": star_links(course_id, star_id),
            "pb": pb_json,
            "attempts": [_attempt_json(a, pbs, clock) for a in in_section],
            "stats": _stats_for(history, stat_menu, clock),
            "strategies": _strategies_for(registered, all_attempts, course_id, star_id),
            "last_strat": service.strat_by_star.get((course_id, star_id)),
            "timeline": _timeline(history, igt_of),
            "markers_by_strat": _markers_for(markers_state, course_id, star_id),
            "progress": _progress(in_section, pb_ids, session_meta, igt_of),
        })
    sections.sort(key=lambda s: last_id.get((s["course_id"], s["star_id"]), -1),
                  reverse=True)

    # segment sections: same shape, RTA-only (segments have no IGT) — pb,
    # attempts, stats, timeline and progress all force the rta clock
    # whatever the view clock. "armed" reads the LIVE projector so a plain
    # view refresh self-heals the UI's armed badge after missed notices.
    seg_defs = {d.id: d for d in service.segment_defs}
    rta_of = lambda a: a.rta_frames
    seg_sections = []
    for seg_id in seen_segs:
        d = seg_defs.get(seg_id)
        history = [a for a in all_attempts if a.segment_id == seg_id]
        in_section = [a for a in history if a in scoped_set]
        pb_row = pbs.get(("segment", seg_id, "rta"))
        seg_sections.append({
            "kind": "segment", "segment_id": seg_id,
            "name": d.name if d else f"segment {seg_id} (deleted)",
            "broken": d is None,
            "armed": seg_id in armed,
            # igt present-as-None: same shape-stability rule as the target
            # payload — UI code reading sec.pb.igt gets null, not undefined.
            "pb": {"igt": None,
                   "rta": ({"frames": pb_row["frames"],
                            "display": format_igt(pb_row["frames"])}
                           if pb_row else None)},
            "attempts": [_attempt_json(a, pbs, "rta") for a in in_section],
            "stats": _stats_for(history, stat_menu, "rta"),
            # observed-from-attempts only (v1): segments have no registered-
            # strategies KV yet; mirrors _strategies_for's observed half.
            "strategies": sorted({a.strat_tag for a in history if a.strat_tag}),
            "last_strat": service.strat_by_segment.get(seg_id),
            "timeline": _timeline(history, rta_of),
            "markers_by_strat": _markers_for(markers_state, "seg", seg_id),
            "progress": _progress(in_section, pb_ids, session_meta, rta_of),
        })
    seg_sections.sort(
        key=lambda s: last_id.get(("segment", s["segment_id"]), -1),
        reverse=True)

    # kind-aware target: the service owns target identity (one builder
    # shared with the target_changed broadcast); the view adds display
    # names and guarantees every key exists for BOTH kinds.
    target = dict(service.target_payload())
    target.setdefault("segment_id", None)
    target.setdefault("segment_name", None)
    tgt_c, tgt_s = target["course_id"], target["star_id"]
    target["course_name"] = course_name(tgt_c) if tgt_c is not None else None
    target["star_name"] = star_name(tgt_c, tgt_s) if tgt_c is not None else None

    return {
        "session": {"id": service.session_id},
        "scope": scope,
        "sessions": sessions_list,
        "clock": clock,
        "target": target,
        "stat_menu": stat_menu,
        "catalog": _CATALOG,
        "stars": sections,
        "segments": seg_sections,
        "unassigned": unassigned,
        "strategies": registered,
        "last_strat_by_star": {f"{c}:{s}": v
                               for (c, s), v in service.strat_by_star.items()},
        "stage": service.current_stage,
    }
