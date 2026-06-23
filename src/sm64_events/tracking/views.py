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
from sm64_events.ranks import classify
from sm64_events.ranks.standards import entity_key
from sm64_events.stats.registry import (DEFAULT_STAT_MENU, REGISTRY,
                                        compute_stat, selection_id,
                                        selection_order)
from sm64_events.tracking.projection import journal_id
from sm64_events.tracking.routes import route_stats

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


def _attempt_json(a, pbs, clock, ranks=None):
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
            "rank": _attempt_rank(a, frames, ranks),
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


def _attempt_rank(a, frames, ranks) -> str | None:
    if ranks is None or frames is None or a.outcome != "success" or not a.strat_tag:
        return None
    ek = entity_key(a.course_id, a.star_id, a.segment_id)
    return classify.rank_for(ranks.ladder_cs(ek, a.strat_tag), classify.display_cs(frames))


def _section_banner(ranks, ek, strat, pb) -> dict | None:
    """Rank banner for a section: the PB time graded under the ACTIVE strat."""
    if ranks is None or not strat or pb is None:
        return None
    ladder = ranks.ladder_cs(ek, strat)
    if not ladder:
        return None
    return classify.band(ladder, classify.display_cs(pb["frames"]))


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


def _progress(attempts, pb_ids: set, session_meta, frames_of,
              ranks=None, clock="igt") -> dict | None:
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
        frames = a.igt_frames if clock == "igt" else a.rta_frames
        by_session.setdefault(a.session_id, []).append({
            "t_utc": a.ended_utc,
            "igt_frames": a.igt_frames,
            "rta_frames": a.rta_frames,
            "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
            "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
            "attempt_id": a.id,
            "is_pb_igt": (a.id, "igt") in pb_ids,
            "is_pb_rta": (a.id, "rta") in pb_ids,
            "rank": _attempt_rank(a, frames, ranks),
        })
    if not by_session:
        return None
    return {"sessions": [
        {"session_id": sid,
         "label": session_meta.get(sid, {}).get("label"),
         "started_utc": session_meta.get(sid, {}).get("started_utc"),
         "points": pts}
        for sid, pts in sorted(by_session.items())]}


# Castle-subarea quick-select: the (level, area) pairs a segment EXPLICITLY
# starts in, read off its start triggers. Only subarea-scoped triggers count —
# a bare "enter Castle Inside" with no subarea must NOT surface the segment in
# every subarea (that is what keeps LBLJ out of Upstairs). Derived from the
# trigger param NAMES (stable across the matcher), so this stays decoupled from
# segments.py's registry:
#   area_enter / attempt_anchor : (level, area)
#   level_enter / level_exit    : (to, to_subarea)   [to_subarea exists once the
#       subarea-trigger work lands; until then .get() returns None and the row
#       contributes nothing — forward-safe]
# The UI (ui/components/stagebanner.js) filters these by the current castle
# subarea (stage_changed carries level+area) to offer one-click segment targets.
def _segment_start_areas(start_triggers: list) -> list:
    out: list = []
    for trig in start_triggers:
        kind = trig.get("type")
        if kind in ("area_enter", "attempt_anchor"):
            level, area = trig.get("level"), trig.get("area")
        elif kind in ("level_enter", "level_exit"):
            level, area = trig.get("to"), trig.get("to_subarea")
        else:
            continue
        if level is not None and area is not None and [level, area] not in out:
            out.append([level, area])
    return out


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
            unassigned.append(_attempt_json(a, pbs, clock, service.ranks))
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
            "attempts": [_attempt_json(a, pbs, clock, service.ranks) for a in in_section],
            "stats": _stats_for(history, stat_menu, clock),
            "strategies": _strategies_for(registered, all_attempts, course_id, star_id),
            "last_strat": service.strat_by_star.get((course_id, star_id)),
            "timeline": _timeline(history, igt_of),
            "markers_by_strat": _markers_for(markers_state, course_id, star_id),
            "progress": _progress(in_section, pb_ids, session_meta, igt_of,
                                  service.ranks, clock),
            "rank": _section_banner(service.ranks, entity_key(course_id, star_id),
                                    service.strat_by_star.get((course_id, star_id)),
                                    pbs.get((course_id, star_id, clock))),
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
            "attempts": [_attempt_json(a, pbs, "rta", service.ranks) for a in in_section],
            "stats": _stats_for(history, stat_menu, "rta"),
            # observed-from-attempts only (v1): segments have no registered-
            # strategies KV yet; mirrors _strategies_for's observed half.
            "strategies": sorted({a.strat_tag for a in history if a.strat_tag}),
            "last_strat": service.strat_by_segment.get(seg_id),
            "timeline": _timeline(history, rta_of),
            "markers_by_strat": _markers_for(markers_state, "seg", seg_id),
            "progress": _progress(in_section, pb_ids, session_meta, rta_of,
                                  service.ranks, "rta"),
            "rank": _section_banner(service.ranks, entity_key(None, None, seg_id),
                                    service.strat_by_segment.get(seg_id),
                                    pbs.get(("segment", seg_id, "rta"))),
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
        # Enabled segments that start in a known subarea, for the castle
        # quick-select banner (filtered client-side by the current subarea).
        "segment_targets": [
            {"segment_id": d.id, "name": d.name, "start_areas": areas}
            for d in service.segment_defs
            if d.enabled and (areas := _segment_start_areas(d.start_triggers))],
    }


def _fmt_ms(ms):
    if ms is None:
        return None
    s, ms = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}.{ms:03d}"


def _resolve_cands(cands, seg_names):
    out = []
    for c in cands:
        if c["type"] == "segment":
            out.append({"kind": "segment", "segment_id": c["segment_id"],
                        "display": seg_names.get(c["segment_id"],
                                                 f"segment {c['segment_id']} (deleted)")})
        else:
            out.append({"kind": "star", "course": c["course"], "star": c["star"],
                        "display": star_name(c["course"], c["star"])})
    return out


def build_run_view(db, service) -> dict:
    """Live run state for the run panel: the active run (resolved step names +
    elapsed + per-step PB-cumulative and gold-duration for ±/gold) plus the
    route's PB total and gold sum-of-best."""
    from sm64_events.tracking.runs import pb_run, gold_splits
    act = service.active_run()
    seg_names = {d["id"]: d["name"] for d in db.segment_defs()}
    offset = service.run_settings()["start_offset_ms"]
    out = {"active": None, "pb": None, "gold": None, "start_offset_ms": offset}
    if act is None:
        return out
    steps_def = next((r["steps"] for r in db.routes()
                      if r["id"] == act["route_id"]), [])
    runs = db.runs(route_id=act["route_id"]) if act["route_id"] is not None else []
    pb = pb_run(runs)
    gold = gold_splits(runs, steps_def)
    pb_cum = {s["step_index"]: s["elapsed_ms"] for s in pb["splits"]} if pb else {}
    gold_dur = gold["durations"]
    steps = []
    for i, s in enumerate(act["steps"]):
        cands = _resolve_cands(steps_def[i]["candidates"], seg_names) \
            if i < len(steps_def) else []
        steps.append({**s, "candidates": cands,
                      "display": cands[0]["display"] if cands else "?",
                      "elapsed_display": _fmt_ms(
                          None if s["elapsed_ms"] is None
                          else s["elapsed_ms"] + offset),
                      "pb_elapsed_ms": pb_cum.get(i),
                      "gold_ms": gold_dur.get(i)})
    out["active"] = {**act, "steps": steps}
    out["pb"] = {"total_ms": pb["total_ms"],
                 "display": _fmt_ms(pb["total_ms"] + offset)} if pb else None
    out["gold"] = {"sum_of_best": gold["sum_of_best"],
                   "display": _fmt_ms(None if gold["sum_of_best"] is None
                                      else gold["sum_of_best"] + offset)}
    return out


def _enrich_splits(run, seg_names):
    """Add display name, duration_ms, and duration_display to each split.

    display: resolved from completed_item (star name or segment name).
    duration_ms: time spent on this step (elapsed_ms minus previous
      split's elapsed_ms, i.e. the wall-clock split for that step).
    duration_display: human-readable duration_ms via _fmt_ms."""
    out, prev = [], 0
    for s in run["splits"]:
        ci = s.get("completed_item") or {}
        if ci.get("type") == "segment":
            disp = seg_names.get(ci.get("segment_id"),
                                 f"segment {ci.get('segment_id')} (deleted)")
        elif ci.get("type") == "star":
            disp = star_name(ci.get("course"), ci.get("star"))
        else:
            disp = "?"
        dur = (s["elapsed_ms"] - prev) if s["elapsed_ms"] is not None else None
        prev = s["elapsed_ms"] if s["elapsed_ms"] is not None else prev
        out.append({**s, "display": disp, "duration_ms": dur,
                    "duration_display": _fmt_ms(dur)})
    return out


def build_run_history(db, route_id: int | None = None) -> dict:
    """Saved runs (optionally one route) + the PB. display_total folds in the
    per-run offset; finished runs flagged is_pb power the progression graph.
    Each run's splits are enriched with display names and per-step durations."""
    from sm64_events.tracking.runs import pb_run
    runs = db.runs(route_id=route_id)
    seg_names = {d["id"]: d["name"] for d in db.segment_defs()}
    out_runs = [{**r,
                 "display_total": _fmt_ms(None if r["total_ms"] is None
                                          else r["total_ms"] + r["start_offset_ms"]),
                 "splits": _enrich_splits(r, seg_names)}
                for r in runs]
    pb = pb_run(runs)
    return {"runs": out_runs,
            "pb": {"total_ms": pb["total_ms"]} if pb else None}


def _candidate_rank(db, service, c) -> str | None:
    """Best rank for one route candidate under that candidate's active strat."""
    if service.ranks is None:
        return None
    if c["type"] == "segment":
        ek = entity_key(None, None, c["segment_id"])
        strat = service.strat_by_segment.get(c["segment_id"])
        pb = db.current_pb(None, None, "rta", segment_id=c["segment_id"])
    else:
        ek = entity_key(c["course"], c["star"])
        strat = service.strat_by_star.get((c["course"], c["star"]))
        pb = db.current_pb(c["course"], c["star"], "igt")
    if not strat or pb is None:
        return None
    ladder = service.ranks.ladder_cs(ek, strat)
    if not ladder:
        return None
    return classify.rank_for(ladder, classify.display_cs(pb["frames"]))


def build_route_view(db, service, route_id: int) -> dict:
    """Resolve a route for display: each step's candidates get names, plus the
    per-step success rate and cumulative product (tracking/routes.route_stats).
    A candidate whose segment was deleted is marked broken (no cascade).
    Each step gains 'rank' (best-ranked candidate); the route view gains
    'avg_rank' (nearest-tier mean of step ranks) and 'weakest_step' index."""
    route = next((r for r in db.routes() if r["id"] == route_id), None)
    if route is None:
        raise LookupError(f"route {route_id} not found")
    attempts = db.attempts()
    seg_names = {d["id"]: d["name"] for d in db.segment_defs()}
    stats = route_stats(route["steps"], attempts)
    steps = []
    for step, st in zip(route["steps"], stats):
        cands, broken = [], False
        for c in step["candidates"]:
            if c["type"] == "segment":
                name = seg_names.get(c["segment_id"])
                if name is None:
                    broken = True
                    name = f"segment {c['segment_id']} (deleted)"
                cands.append({"kind": "segment", "segment_id": c["segment_id"],
                              "display": name})
            else:
                cands.append({"kind": "star", "course": c["course"],
                              "star": c["star"],
                              "display": star_name(c["course"], c["star"]),
                              "course_name": course_name(c["course"])})
        ranks_here = [_candidate_rank(db, service, c) for c in step["candidates"]]
        best = max((r for r in ranks_here if r),
                   key=lambda r: classify.RANK_SCORE[r], default=None)
        steps.append({"label": step.get("label"), "need": step["need"],
                      "candidates": cands, "step_rate": st["step_rate"],
                      "cumulative": st["cumulative"], "broken": broken,
                      "rank": best})
    scored = [classify.RANK_SCORE[s["rank"]] for s in steps if s["rank"]]
    avg_rank = None
    weakest_step = None
    if scored:
        mean = sum(scored) / len(scored)
        tier = min(classify.RANK_SCORE, key=lambda n: abs(classify.RANK_SCORE[n] - mean))
        avg_rank = {"score": round(mean, 1), "tier": tier}
        ranked = [(i, classify.RANK_SCORE[s["rank"]]) for i, s in enumerate(steps)
                  if s["rank"]]
        weakest_step = min(ranked, key=lambda t: t[1])[0]
    return {"id": route["id"], "name": route["name"],
            "start_condition": route["start_condition"], "steps": steps,
            "avg_rank": avg_rank, "weakest_step": weakest_step}
