"""Shared clause + step constructors for the seeded corpus tables.

ONE place that knows the seed JSON shapes, so 56 movement segments and 48
routes cannot disagree about them. Ids are the addresses.py id spaces: LEVEL
ids for triggers, COURSE ids + 0-based star ids for star candidates.

Consumed by tools/corpus_{legacy,movements,routes_main,routes_stage}.py and
expanded by tools/build_defaults_seed.py.
"""

CASTLE = 6                           # LEVEL_CASTLE_INSIDE
LOBBY, UPSTAIRS, BASEMENT = 1, 2, 3  # CASTLE_AREA_NAMES ids

MAIN = "Main Categories"
STAGE_RTA = "Stage RTA"

# Sub-categories are a PATH inside the existing free-text `category` field
# (user request 2026-07-24) — "Main Categories/16 Star", "Stage RTA/Wet-Dry
# World". No migration, no API change, no second column, and it generalises to
# any depth; the UI splits on the separator and nests the collapsible groups.
# A category name containing "/" would be ambiguous — don't use one.
CATEGORY_SEP = "/"


def sub(top: str, name: str) -> str:
    """"Main Categories" + "16 Star" -> "Main Categories/16 Star"."""
    return f"{top}{CATEGORY_SEP}{name}"
CASTLE_MOVEMENT = "Castle Movement"
TRICKS = "Tricks"
BOWSER_FIGHTS = "Bowser Fights"
# The 100-coin "grab a different star to actually leave" pattern (Task 20,
# spec 2026-07-28-multi-step-segments) deliberately ENDS on star_grabbed --
# see mechanic()'s docstring for why that disqualifies it from Castle
# Movement. Its own category keeps it out of corpus_movements.MOVEMENTS
# (and therefore out of every test built on "a movement never ends on a
# star grab").
HUNDRED_COIN_EXIT = "100 Coin Exit"

ROUTE_SCOPED = [{"type": "in_active_route"}]

# There is basically one way to do a castle movement, so every movement ships
# with this already picked rather than with the picker in its red "you forgot"
# state — and, because untagged PBs are skipped by the per-strategy ranking, a
# movement can now actually be ranked. "Standard" is already the corpus-wide
# name for the ordinary way to do something (48 of the 102 rank-standard
# entities use it). Legacy trick defs deliberately get none: several have real
# competing strategies. Spec 2026-07-24-segment-default-strat-design.md.
STANDARD_STRAT = "Standard"

# Every movement shipped match_mode="loose" from 2026-07-28 (spec
# ...-multi-step-segments, Task 19) until 2026-08-02, when Griffin ruled the
# opposite: *"That is a fixed path, and there are no other options. I want it
# to be very strict... In that situation, we're testing a specific path. There
# are no deviations that are allowed."*
#
# Loose was right about the waypoint-cancellation rules being too eager and
# wrong about what replaces them. A movement's identity IS its route, and
# nothing but a DECLARATION can express that: entering BitFS during `Bowser 2
# -> Upstairs` is the same move whether it is the fastest route or a wrong
# turn, so no measurement separates them. `via=[...]` is that declaration, and
# under the path cursor (spec 2026-08-02-strict-path-segments) it is REQUIRED
# rather than merely permitted -- running the movement any other way records
# nothing, and a runner who wants both times authors two segments.
#
# Losing the staleness deadline comes with the flip and is accepted ("removing
# staleness deadline is reasonable"): `_deadline_for` returns None for anything
# but loose, so a strict arm persists while the player stands still. Harmless
# under the path rule, since any wrong move voids it.
#
# `movement()`'s own match_mode= parameter overrides this per row -- the case
# that matters is a recording promoted through tools/corpus_from_db.py, which
# must carry whatever mode it was recorded and verified with rather than
# silently reconciling back to this default.
DEFAULT_MOVEMENT_MATCH_MODE = "strict"


# --- trigger clauses -------------------------------------------------------

def exit_level(level, to=None):
    clause = {"type": "level_exit", "from": level}
    if to is not None:
        clause["to"] = to
    return clause


def enter_level(level, frm=None, to_subarea=None):
    """`to_subarea` pins WHICH castle area the entry lands in.

    The match lambda ignores it on anything but a start trigger (the castle
    loads the lobby transiently before warping, so the destination area is
    only knowable a poll later -- see TRIGGERS in tracking/segments.py). It is
    still load-bearing on a STEP, because `segments.step_node` reads it and the
    path cursor compares that node against the SETTLED position. Without it a
    castle step resolves to the bare node "6", which is not in the world graph
    at all, so it could never match and would void every run of the movement.

    A castle step reached by WALKING inside the castle is an `enter_area`
    instead. This one is for arriving from another LEVEL -- the courtyard, the
    grounds, a course -- where `can_run_from` refuses an `area_enter` next step
    because it can only ever fire from level 6."""
    clause = {"type": "level_enter", "to": level}
    if to_subarea is not None:
        clause["to_subarea"] = to_subarea
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


def enter_entrance(level):
    """The ENTRANCE TOUCH that leads to `level` -- the frame Mario collides
    with its painting, portal, hole or pipe, 77 frames (23 at a pipe) before
    the level loads. A movement ends HERE rather than on the load, so its
    recorded time is the travelling and not the fade (task 0081).

    ONE clause param, because the entrance's own level is DERIVED
    (`topology.entrance_level`, the same door `segments.fires_from` checks arm
    positions against) rather than authored. Asking for it was the live
    complaint of 2026-08-05: three controls on one row, the middle one
    demanding you already know the DDD portal lives in the castle interior.
    """
    from sm64_events.tracking.topology import entrance_level
    if entrance_level(level) is None:
        raise AssertionError(
            f"level {level} has no single castle-side entrance; the world "
            f"graph or this call is wrong")
    return {"type": "entrance_touched", "to": level}


def grab_key(level):
    return {"type": "key_grabbed", "level": level}


def anchor(level, area=None):
    clause = {"type": "attempt_anchor", "level": level}
    if area is not None:
        clause["area"] = area
    return clause


def spawn(level):
    return {"type": "spawned", "level": level}


def moment(kind, level=None, area=None, ordinal=None):
    """A mid-course boundary: a door opening, a textbox firing.

    `kind` is a row in detectors/moment.py's MOMENTS registry, which is the
    one place moments are named -- this helper never carries a list of its
    own. `ordinal` unset means any occurrence.
    """
    clause = {"type": "moment_reached", "kind": kind}
    if level is not None:
        clause["level"] = level
    if area is not None:
        clause["area"] = area
    if ordinal is not None:
        clause["ordinal"] = ordinal
    return clause


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


def refuse_a_course_load(seed_key, end):
    """A segment that ENDS somewhere may not end on the course LOAD (task
    0081, 2026-08-04). A movement or a trick is the travelling, and
    `level_enter` fires 77 frames after the travelling stopped -- so ending
    there banks the painting fade as if it were movement. `enter_entrance` is
    the same moment a runner would call it.

    This REFUSES rather than warning because the mistake is invisible
    afterwards: the row still matches, still records, and is simply 2.6 s slow
    forever. Called by both `movement()` and corpus_legacy's `_seg()`, so
    there is one door rather than a rule each author has to remember.

    Ending inside the CASTLE is untouched -- there is no entrance to touch
    (Lakitu Skip ends on entering level 6, and an `enter_area` end never
    reaches here at all).
    """
    from sm64_events.memory.addresses import CASTLE_REGION_LEVELS
    for clause in (end if isinstance(end, list) else [end]):
        if clause.get("type") == "level_enter" \
                and clause.get("to") not in CASTLE_REGION_LEVELS:
            raise AssertionError(
                f"{seed_key}: ending on entering level {clause['to']} "
                f"measures 77 frames of painting fade as travelling. Use "
                f"enter_entrance({clause['to']}) -- the entrance touch -- "
                f"rather than enter_level.")


def movement(seed_key, name, start, end, via=(), match_mode=None):
    """A castle-movement segment. `via` is a FLAT list of clauses; each becomes
    a single-clause waypoint (no corpus movement needs an any-of waypoint).

    `match_mode=None` (the default) means "use the corpus default" --
    `_movement_row` (tools/build_defaults_seed.py) stamps
    DEFAULT_MOVEMENT_MATCH_MODE on every row that doesn't say otherwise. Pass
    an explicit value to override it for ONE movement; `corpus_from_db.py`
    prints this argument back out whenever a live def's mode differs from the
    default, so the override round-trips instead of silently reconciling away.

    A movement may NOT end on entering a course (task 0081, 2026-08-04) --
    enforced by `refuse_a_course_load`, which `build_defaults_seed` calls over
    every assembled row rather than this constructor calling it directly. The
    difference matters: `tools/corpus_from_db.py` reconstructs corpus SOURCE
    from a live db and must be able to print a pre-sweep definition faithfully,
    so the authoring policy belongs to the build, not to the row shape.
    """
    row = {"seed_key": seed_key, "name": name, "start": start,
           "via": list(via), "end": end}
    if match_mode is not None:
        row["match_mode"] = match_mode
    return row


def mechanic(seed_key, name, start, end, category, match_mode="loose", via=()):
    """A non-route segment describing an intrinsic game mechanic (Task 20,
    spec 2026-07-28-multi-step-segments): the "100 coins doesn't end the
    level, a different star does" pattern and the Bowser-stage "the reds
    star doesn't end it, the pipe does" pattern. Differences from
    movement()/`_movement_row`:

    (1) `start`/`end` may each be a genuine any-of LIST of clauses (the
        100-coin case needs six alternative stars, since the vocabulary has
        no "any star but this one" clause; the corpus reshape (2026-07-29,
        same spec) put the whole-course/whole-stage span's arm on the
        `[level_enter, attempt_anchor]` any-of idiom `_seg()` already uses,
        so `start` needed the same list-or-single flexibility `end` already
        had) -- movement() assumes exactly one clause per side, full seed
        shape here instead of a compact row `_movement_row` expands later.
    (2) `via` is a FLAT list of clauses, same convention as movement()'s --
        each becomes a single-clause waypoint. Added 2026-07-29 so the reds-
        star grab (reds->pipe) and the 100-coin grab (100c->exit) could
        become the WAYPOINT between stage-entry and the real end, instead of
        being the start trigger itself -- starting on the grab is what made
        both families unselectable before the grab and (for reds->pipe) what
        timed only star->pipe instead of the whole stage (live report
        2026-07-29). `[[clause] for clause in via]` mirrors `_movement_row`.
    (3) UNGUARDED (guards=[]), like the ten legacy segments, not route-scoped
        like the 56 castle movements: there is exactly ONE way to do either
        (one pipe per Bowser stage, "some other star" for the 100-coin
        case), so there is no route ambiguity to scope against -- the same
        reason seg:bowser-1/seg:bitdw-pipe/etc. stay always-armed."""
    return {"seed_key": seed_key, "name": name, "enabled": True,
            "start_triggers": start if isinstance(start, list) else [start],
            "end_triggers": end if isinstance(end, list) else [end],
            "waypoints": [[clause] for clause in via],
            "guards": [], "category": category,
            "match_mode": match_mode}
