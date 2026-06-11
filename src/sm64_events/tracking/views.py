"""Builds the GET /api/session payload. Times lists are session-scoped;
stat chips compute over the star's full history (lifetime), per spec §8."""
from sm64_events.core.timefmt import format_igt
from sm64_events.links import star_links
from sm64_events.memory.addresses import (COURSE_NAMES, STAR_NAMES,
                                          course_name, star_name)
from sm64_events.stats.registry import DEFAULT_STAT_MENU, REGISTRY, compute_stat

# Timeline markers (per-star event graph): outcome -> IGT extractor.
# Adding a marker kind is one row here (+ a style row in ui timeline.js).
# The axis is IGT-based by design: resets/deaths only have an IGT position.
TIMELINE_OUTCOMES = {
    "success": lambda a: a.igt_frames,
    "reset": lambda a: a.igt_frames,
    "death": lambda a: a.igt_frames,
}


def _timeline(history) -> dict | None:
    """X axis 0 -> longest SUCCESSFUL grab; every qualifying attempt is a
    point at its IGT position. Points may exceed max_frames (a reset later
    than the best success) — the UI extends the axis as needed."""
    points = []
    for a in history:
        if a.cleared or a.outcome not in TIMELINE_OUTCOMES:
            continue
        frames = TIMELINE_OUTCOMES[a.outcome](a)
        if frames is None:
            continue
        points.append({"frames": frames, "igt": format_igt(frames),
                       "outcome": a.outcome, "attempt_id": a.id})
    if not points:
        return None
    succ = [p["frames"] for p in points if p["outcome"] == "success"]
    max_frames = max(succ) if succ else max(p["frames"] for p in points)
    return {"max_frames": max_frames, "max_display": format_igt(max_frames),
            "points": points}


def _fmt(value, fmt):
    if value is None:
        return None
    if fmt == "time":
        return format_igt(round(value))
    if fmt == "percent":
        return f"{round(value * 100)}%"
    return str(value)


def _current_pbs(db) -> dict:
    """(course, star, mode) -> latest pb row."""
    out = {}
    for row in db.pbs():  # ordered by id: later rows win
        out[(row["course_id"], row["star_id"], row["timer_mode"])] = row
    return out


def _attempt_json(a, pbs, clock):
    pb = pbs.get((a.course_id, a.star_id, clock))
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
            "cleared_reason": a.cleared_reason, "ended_utc": a.ended_utc}


def _catalog() -> dict:
    courses = []
    for cid, cname in COURSE_NAMES.items():
        n = len(STAR_NAMES.get(cid, ()))
        if 1 <= cid <= 15:
            n = 7  # six named stars + 100 coins
        courses.append({"id": cid, "name": cname,
                        "stars": [star_name(cid, s) for s in range(max(n, 1))]})
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


def build_session_view(db, service, clock: str, scope: str = "session") -> dict:
    all_attempts = db.attempts()
    session_attempts = [a for a in all_attempts
                        if a.session_id == service.session_id]
    # scoped determines which attempts drive the seen-set, in_section lists,
    # and unassigned list. Stats always use lifetime (all_attempts).
    scoped = all_attempts if scope == "lifetime" else session_attempts
    pbs = _current_pbs(db)
    stat_menu = db.get_state("stat_menu", default=DEFAULT_STAT_MENU)
    registered = db.get_state("strategies", {})

    sections, unassigned = [], []
    seen: dict[tuple[int, int], None] = {}
    for a in scoped:
        if a.course_id is None:
            unassigned.append(_attempt_json(a, pbs, clock))
        else:
            seen[(a.course_id, a.star_id)] = None

    for course_id, star_id in seen:
        history = [a for a in all_attempts
                   if a.course_id == course_id and a.star_id == star_id]
        in_section = [a for a in history if a in set(scoped)]
        stats = []
        for sel in stat_menu:
            if sel["key"] not in REGISTRY:
                continue
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
        pb_json = {}
        for mode in ("igt", "rta"):
            row = pbs.get((course_id, star_id, mode))
            pb_json[mode] = ({"frames": row["frames"],
                              "display": format_igt(row["frames"])}
                             if row else None)
        sections.append({
            "course_id": course_id, "star_id": star_id,
            "course_name": course_name(course_id),
            "star_name": star_name(course_id, star_id),
            "links": star_links(course_id, star_id),
            "pb": pb_json,
            "attempts": [_attempt_json(a, pbs, clock) for a in in_section],
            "stats": stats,
            "strategies": _strategies_for(registered, all_attempts, course_id, star_id),
            "last_strat": service.strat_by_star.get((course_id, star_id)),
            "timeline": _timeline(history),
        })

    tgt_c, tgt_s = service.target if service.target else (None, None)
    return {
        "session": {"id": service.session_id},
        "scope": scope,
        "sessions": db.sessions(),
        "clock": clock,
        "target": {"course_id": tgt_c, "star_id": tgt_s,
                   "course_name": course_name(tgt_c) if tgt_c is not None else None,
                   "star_name": star_name(tgt_c, tgt_s) if tgt_c is not None else None,
                   "strat_tag": service.strat_tag},
        "stat_menu": stat_menu,
        "catalog": _CATALOG,
        "stars": sections,
        "unassigned": unassigned,
        "strategies": registered,
        "last_strat_by_star": {f"{c}:{s}": v
                               for (c, s), v in service.strat_by_star.items()},
    }
