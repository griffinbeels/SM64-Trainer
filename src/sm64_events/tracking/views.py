"""Builds the GET /api/session payload. Times lists are session-scoped;
stat chips compute over the star's full history (lifetime), per spec §8."""
from sm64_events.core.timefmt import format_igt
from sm64_events.links import star_links
from sm64_events.memory.addresses import (COURSE_NAMES, STAR_NAMES,
                                          course_name, star_name)
from sm64_events.stats.registry import DEFAULT_STAT_MENU, REGISTRY, compute_stat


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
    delta = (frames - pb["frames"]
             if pb and frames is not None and a.outcome == "success" else None)
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


def build_session_view(db, service, clock: str) -> dict:
    all_attempts = db.attempts()
    session_attempts = [a for a in all_attempts
                        if a.session_id == service.session_id]
    pbs = _current_pbs(db)
    stat_menu = db.get_state("stat_menu", default=DEFAULT_STAT_MENU)

    sections, unassigned = [], []
    seen: list[tuple[int, int]] = []
    for a in session_attempts:
        if a.course_id is None:
            unassigned.append(_attempt_json(a, pbs, clock))
        elif (a.course_id, a.star_id) not in seen:
            seen.append((a.course_id, a.star_id))

    for course_id, star_id in seen:
        history = [a for a in all_attempts
                   if a.course_id == course_id and a.star_id == star_id]
        in_session = [a for a in history if a.session_id == service.session_id]
        stats = []
        for sel in stat_menu:
            if sel["key"] not in REGISTRY:
                continue
            d = REGISTRY[sel["key"]]
            value = compute_stat(sel["key"], history, sel.get("params"), clock)
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
            "attempts": [_attempt_json(a, pbs, clock) for a in in_session],
            "stats": stats,
        })

    tgt_c, tgt_s = service.target if service.target else (None, None)
    return {
        "session": {"id": service.session_id},
        "clock": clock,
        "target": {"course_id": tgt_c, "star_id": tgt_s,
                   "course_name": course_name(tgt_c) if tgt_c is not None else None,
                   "star_name": star_name(tgt_c, tgt_s) if tgt_c is not None else None,
                   "strat_tag": service.strat_tag},
        "stat_menu": stat_menu,
        "catalog": _catalog(),
        "stars": sections,
        "unassigned": unassigned,
    }
