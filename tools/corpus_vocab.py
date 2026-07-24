"""Shared clause + step constructors for the seeded corpus tables.

ONE place that knows the seed JSON shapes, so 55 movement segments and ~50
routes cannot disagree about them. Ids are the addresses.py id spaces: LEVEL
ids for triggers, COURSE ids + 0-based star ids for star candidates.

Consumed by tools/corpus_{legacy,movements,routes_main,routes_stage}.py and
expanded by tools/build_defaults_seed.py.
"""

CASTLE = 6                           # LEVEL_CASTLE_INSIDE
LOBBY, UPSTAIRS, BASEMENT = 1, 2, 3  # CASTLE_AREA_NAMES ids

MAIN = "Main Categories"
STAGE_RTA = "Stage RTA"
CASTLE_MOVEMENT = "Castle Movement"
TRICKS = "Tricks"
BOWSER_FIGHTS = "Bowser Fights"

ROUTE_SCOPED = [{"type": "in_active_route"}]


# --- trigger clauses -------------------------------------------------------

def exit_level(level, to=None):
    clause = {"type": "level_exit", "from": level}
    if to is not None:
        clause["to"] = to
    return clause


def enter_level(level, frm=None):
    clause = {"type": "level_enter", "to": level}
    if frm is not None:
        clause["from"] = frm
    return clause


def enter_area(area, frm=None):
    clause = {"type": "area_enter", "level": CASTLE, "area": area}
    if frm is not None:
        clause["from"] = frm
    return clause


def grab_star(course, star_id):
    return {"type": "star_grabbed", "course": course, "star": star_id}


def enter_warp(level):
    return {"type": "warp_entered", "level": level}


def grab_key(level):
    return {"type": "key_grabbed", "level": level}


def anchor(level, area=None):
    clause = {"type": "attempt_anchor", "level": level}
    if area is not None:
        clause["area"] = area
    return clause


def spawn(level):
    return {"type": "spawned", "level": level}


# --- route steps -----------------------------------------------------------

def star(course, star_id, label):
    """One star, one step."""
    return {"label": label, "need": 1,
            "candidates": [{"type": "star", "course": course, "star": star_id}]}


def stars(course, star_ids, label, need=None):
    """A group. `need` defaults to ALL of them — that is the "+ 100 Coins"
    shape (both stars come from the same visit, in whichever order the coin
    count crosses). Pass need=1 for a documented either/or."""
    candidates = [{"type": "star", "course": course, "star": s}
                  for s in star_ids]
    return {"label": label,
            "need": len(candidates) if need is None else need,
            "candidates": candidates}


def segment(seed_key, label):
    """A movement or trick step. resolve_steps rewrites seed_key -> segment_id
    at reconcile, so the seed never needs to know local autoincrement ids."""
    return {"label": label, "need": 1,
            "candidates": [{"type": "segment", "seed_key": seed_key}]}


def _merge_label(labels, need):
    """"WF — Blast Away the Wall" + five siblings -> "WF — 6 stars".

    Constituent labels are "<stage> — <star>"; when they all agree on the
    stage, the group takes that stage and a count. Anything else (the castle-
    secret stars, which carry no stage prefix) joins its labels."""
    heads = {label.split(" — ")[0] for label in labels if " — " in label}
    if len(heads) == 1 and all(" — " in label for label in labels):
        return f"{heads.pop()} — {need} stars"
    return " + ".join(labels)


def group_visits(steps):
    """Collapse each run of consecutive star steps from ONE course into a
    single "collect all of these" group (user decision 2026-07-24).

    A route never dictates the order stars are grabbed WITHIN a stage visit —
    you enter Whomp's Fortress and leave with six stars, and which came first
    is the player's business. Only the sequence of visits is the route. So a
    course visit is one step with need = every star it wants, and the K-of-N
    machinery already in RunTracker does the rest, in any order.

    Two visits to the same course stay two steps: they are separated by the
    movement step between them, which breaks the run. A documented either/or
    inside a visit survives as need < len(candidates) — "any 4 of these 5" —
    which is exactly what it means. A lone star keeps its own step."""
    out, run, run_course = [], [], None

    def flush():
        nonlocal run, run_course
        if len(run) == 1:
            out.append(run[0])
        elif run:
            candidates = [c for step in run for c in step["candidates"]]
            need = sum(step["need"] for step in run)
            out.append({"label": _merge_label([s["label"] for s in run], need),
                        "need": need, "candidates": candidates})
        run, run_course = [], None

    for step in steps:
        courses = {c["course"] for c in step["candidates"]
                   if c["type"] == "star"}
        if len(courses) == 1 and len(courses) == len(
                {c["type"] for c in step["candidates"]}):
            course = courses.pop()
            if run and course != run_course:
                flush()
            run, run_course = run + [step], course
        else:
            flush()
            out.append(step)
    flush()
    return out


def route(seed_key, name, category, steps, start_condition=None):
    """Every seeded route is grouped by visit — see group_visits."""
    return {"seed_key": seed_key, "name": name, "category": category,
            "start_condition": start_condition or {"type": "reset_game"},
            "steps": group_visits(steps)}


def movement(seed_key, name, start, end, via=()):
    """A castle-movement segment. `via` is a FLAT list of clauses; each becomes
    a single-clause waypoint (no corpus movement needs an any-of waypoint)."""
    return {"seed_key": seed_key, "name": name, "start": start,
            "via": list(via), "end": end}
