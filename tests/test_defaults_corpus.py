"""Behavioural gate for the seeded corpus.

The events fed here are synthesized from an INDEPENDENT world model — the
castle topology in addresses.py plus how a real walk between two nodes
actually looks on the wire — NOT from the definition under test. A definition
that merely agrees with itself proves nothing; what has to be proven is that
each movement survives the intermediate `area_changed` and `level_changed`
events a real player generates between its own checkpoints, because those are
exactly what the matcher disarms on.

The definition contributes only its CHECKPOINTS (start, waypoints, end) — the
author's stated plan. Everything between them is derived here."""
import json
from collections import deque
from dataclasses import dataclass

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.core.timefmt import format_igt
from sm64_events.memory.addresses import (ACT_DISAPPEARED,
                                          CASTLE_REGION_LEVELS,
                                          COURSE_BY_LEVEL,
                                          LEVEL_CASTLE_INSIDE,
                                          WORLD_EDGES_ONE_WAY,
                                          WORLD_EDGES_TWO_WAY,
                                          WORLD_PAUSE_EXITS)
from sm64_events.tracking.lint import lint_definition
from sm64_events.tracking.segments import (MatchContext, SegmentDef,
                                           SegmentEngine, budget_frames,
                                           validate_definition)

SEED = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
SEGMENTS = SEED["segments"]
# The 55 route-scoped movements; the three legacy Castle Movement rows
# (the pipe entries and BitS Entry) carry no guard and are covered elsewhere.
MOVEMENTS = [s for s in SEGMENTS
             if s["category"] == "Castle Movement" and s["guards"]]

HUB_LEVELS = (16, 26)          # castle grounds, courtyard — no subareas


# --- the independent world model -------------------------------------------

def _node(spec):
    """Registry shorthand: a bare level id means (level, no subarea)."""
    return spec if isinstance(spec, tuple) else (spec, None)


def _graph():
    adjacency: dict[tuple, list] = {}

    def link(src, dst):
        adjacency.setdefault(src, [])
        if dst not in adjacency[src]:
            adjacency[src].append(dst)

    for node_a, node_b in WORLD_EDGES_TWO_WAY:
        link(_node(node_a), _node(node_b))
        link(_node(node_b), _node(node_a))
    for src, dst in WORLD_EDGES_ONE_WAY:
        link(_node(src), _node(dst))
        adjacency.setdefault(_node(dst), [])
    return adjacency


GRAPH = _graph()

# The pause-exit rows as (source, destination) node pairs, so `exit_node` can
# leave them out of "where does leaving this level put you".
PAUSE_EXIT_EDGES = {(_node(src), _node(dst)) for src, dst in WORLD_PAUSE_EXITS}


def path(start: tuple, goal: tuple) -> list:
    """Shortest node path start -> goal over the world topology."""
    if start == goal:
        return [start]
    seen, queue = {start}, deque([[start]])
    while queue:
        route = queue.popleft()
        for nxt in GRAPH.get(route[-1], ()):
            if nxt in seen:
                continue
            if nxt == goal:
                return route + [nxt]
            seen.add(nxt)
            queue.append(route + [nxt])
    raise AssertionError(f"no path {start} -> {goal}")


def exit_node(level: int) -> tuple:
    """Where the player stands after LEAVING `level` — its entrance region.
    Derived from the topology: the castle-interior neighbour if there is one
    (every painting), else the hub it opens onto (BBH -> courtyard, VCUtM ->
    grounds, CotMC -> HMC).

    Bowser 1 and 2 leave by their one-way KEY-CUTSCENE edge into the castle.
    Bowser 3 has no such edge at all (2026-08-02 correction: winning ends the
    game, losing drops you back into Bowser in the Sky), so it falls through to
    its only neighbour, the course itself.

    The PAUSE EXIT is deliberately not considered here, though it is a real
    edge out of every one of these (2026-08-02). It is a different mechanism:
    the door is how you ordinarily leave, and a walker that took the pause exit
    would route BBH -> Lobby and never see the Courtyard. Movements that really
    do use it declare it as a step, and the walk routes through declared steps.
    """
    neighbours = [n for n in GRAPH.get((level, None), [])
                  if ((level, None), n) not in PAUSE_EXIT_EDGES]
    for candidate in neighbours:
        if candidate[0] == LEVEL_CASTLE_INSIDE:
            return candidate
    for candidate in neighbours:
        if candidate[0] in HUB_LEVELS:
            return candidate
    return neighbours[0]


def clause_node(clause: dict) -> tuple:
    """The node a checkpoint clause puts the player at."""
    kind = clause["type"]
    if kind == "level_enter":
        return (clause["to"], None) if clause["to"] != LEVEL_CASTLE_INSIDE \
            else (LEVEL_CASTLE_INSIDE, 1)
    if kind == "warp_entered":
        # An ENTRANCE TOUCH fires 77 frames before Mario arrives (23 at a
        # pipe), but the walk it ends is the walk to the DESTINATION -- the
        # same node the level_enter it replaced resolved to, which is also
        # what segments.step_node answers for it.
        return (clause["to"], None)
    if kind == "level_exit":
        return exit_node(clause["from"])
    if kind == "area_enter":
        return (LEVEL_CASTLE_INSIDE, clause["area"])
    raise AssertionError(f"no node for {clause!r}")


# --- events ----------------------------------------------------------------

@dataclass
class Ev:
    """Minimal journal-event stand-in — the fields SegmentEngine reads."""
    id: int
    type: str
    frame: int
    payload: dict
    wall_time_utc: str = "2026-07-24T00:00:00Z"
    session_id: int = 1


class _Walker:
    """Emits the events a player produces moving between world nodes.

    Frame discipline matters as much as event order: a level entry and the
    area event that establishes its destination share ONE game frame
    (detectors/level.py then detectors/area.py, same tick), and the engine
    re-pins an arm's recorded area only for a co-frame area event
    (SegmentEngine.feed). Emitting them a frame apart would leave every arm's
    area as None and make the relocation disarm untestable — i.e. it would
    make this whole file pass vacuously."""

    def __init__(self, at: tuple):
        self.at = at
        self.frame = 100
        self.nodes = [at]
        self.events: list[Ev] = []

    def _add(self, kind, payload, same_frame=False):
        if not same_frame:
            self.frame += 1
        self.events.append(Ev(len(self.events) + 1, kind, self.frame, payload))

    def hop(self, target: tuple) -> None:
        for nxt in path(self.at, target)[1:]:
            if nxt[0] != self.at[0]:
                # A crossing INTO a course, secret level, Bowser stage or
                # arena goes through an ENTRANCE Mario touches -- a painting,
                # portal, hole or pipe -- 77 frames earlier, 23 at a pipe. A
                # crossing OUT into the castle does not: an exit is a cutscene
                # or a door. The discriminator is the DESTINATION, read off
                # the world model rather than off the definition being
                # checked, which is this file's whole point.
                if nxt[0] not in CASTLE_REGION_LEVELS:
                    self._add("warp_entered",
                              {"level": self.at[0], "area": self.at[1] or 1,
                               "action": ACT_DISAPPEARED, "to": nxt[0],
                               "igt_frames": 0, "igt": format_igt(0),
                               "igt_source": "counter"})
                self._add("level_changed", {"from": self.at[0], "to": nxt[0]})
                self.at = nxt
                # detectors/area.py establishes the destination area on the
                # SAME tick (from == to: bookkeeping, not a crossing), for
                # EVERY level and not only the castle. Counted in the live
                # journal 2026-08-02: area_changed fires for all 29 levels
                # seen, courses and hubs included. It used to be emitted only
                # for level 6 here, which was harmless while the settled
                # position only mattered inside the castle and became a
                # vacuous pass once the path cursor started reading it
                # everywhere -- a movement out of BBH had no settled node at
                # all until it reached the Lobby, so its first declared step
                # was judged against the wrong place.
                self._add("area_changed",
                          {"level": nxt[0], "from": nxt[1] or 1,
                           "to": nxt[1] or 1, "from_transient": True},
                          same_frame=True)
            else:
                self._add("area_changed",
                          {"level": LEVEL_CASTLE_INSIDE, "from": self.at[1],
                           "to": nxt[1], "from_transient": False})
                self.at = nxt
            self.nodes.append(self.at)

    def grab_star(self, course: int, star_id: int) -> None:
        self._add("star_collected", {"course_id": course, "star_id": star_id,
                                     "num_stars": 0})


def movement_walk(row: dict):
    """(events, start_level, start_area, nodes) for a real performance of
    `row`. Only the CHECKPOINTS come from the definition; every event between
    them — and the frame each lands on — is derived from the topology."""
    start = row["start_triggers"][0]
    checkpoints = [step[0] for step in row["waypoints"]] + [row["end_triggers"][0]]

    if start["type"] == "level_exit":
        origin = (start["from"], None)
        walker = _Walker(origin)
        walker.hop(exit_node(start["from"]))
    elif start["type"] == "level_enter":
        origin = (start["from"], None)
        walker = _Walker(origin)
        walker.hop(clause_node(start))
    elif start["type"] == "star_grabbed":
        # both castle-secret starts (MIPS 1st / 2nd) happen in the basement
        origin = (LEVEL_CASTLE_INSIDE, 3)
        walker = _Walker(origin)
        walker.grab_star(start["course"], start["star"])
    else:
        raise AssertionError(f"unhandled start {start!r}")

    for clause in checkpoints:
        walker.hop(clause_node(clause))
    return walker.events, origin[0], origin[1], walker.nodes


def run_engine(seed_row, events, level, area):
    """Feed `events` to a one-def engine, tracking level/area exactly as the
    projector does, and return the closed attempts. Guards are dropped: the
    in_active_route arm gate is proven in test_segments.py, and here every
    movement is assumed to be in the active route.

    match_mode is threaded from the seed row itself (Task 19) rather than
    left at SegmentDef's "strict" default -- since all 56 movements now ship
    match_mode="loose", this is the line that actually puts the independent
    world-model walk through the loose matcher instead of quietly continuing
    to simulate the old strict one."""
    definition = SegmentDef(
        id=1, name=seed_row["name"], enabled=True,
        start_triggers=seed_row["start_triggers"],
        end_triggers=seed_row["end_triggers"],
        waypoints=seed_row["waypoints"], guards=[],
        match_mode=seed_row.get("match_mode", "strict"))
    engine = SegmentEngine([definition])
    closed = []
    for ev in events:
        prev_level = level
        if ev.type == "level_changed":
            level = ev.payload["to"]
        if ev.type == "area_changed":
            area = ev.payload["to"]
        ctx = MatchContext(level=level, prev_level=prev_level, num_stars=0,
                           area=area)
        got, _ = engine.feed(ev, ctx)
        closed.extend(got)
    return closed


# --- layer 1: structural ---------------------------------------------------

def test_every_seeded_segment_validates():
    for row in SEGMENTS:
        validate_definition(row)


def test_every_movement_is_route_scoped():
    assert len(MOVEMENTS) == 56          # 55 + seg:bowser2->bits (Task 20)
    for row in MOVEMENTS:
        assert row["guards"] == [{"type": "in_active_route"}], row["seed_key"]


def test_route_candidates_all_resolve():
    keys = {s["seed_key"] for s in SEGMENTS}
    for route in SEED["routes"]:
        for step in route["steps"]:
            for cand in step["candidates"]:
                if cand["type"] == "segment":
                    assert cand["seed_key"] in keys, (route["seed_key"], cand)


# --- the model itself ------------------------------------------------------

def test_exit_node_matches_the_castle_layout():
    """Pins the model, so a wrong walk cannot quietly excuse a wrong def."""
    assert exit_node(9) == (6, 1)      # BoB -> lobby
    assert exit_node(8) == (6, 3)      # SSL -> basement
    assert exit_node(10) == (6, 2)     # SL -> upstairs
    assert exit_node(4) == (26, None)  # BBH -> courtyard
    assert exit_node(18) == (16, None)  # VCUtM -> grounds
    assert exit_node(30) == (6, 1)     # Bowser 1 arena -> lobby
    assert exit_node(33) == (6, 3)     # Bowser 2 arena -> basement
    # NOT the castle: Bowser 3 has no exit into it. Winning ends the game and
    # losing drops you back into Bowser in the Sky, so its only neighbour is
    # the course (human correction 2026-08-02, reading tools/topology_map.py).
    assert exit_node(34) == (21, None)  # Bowser 3 arena -> Bowser in the Sky


def test_path_crosses_the_lobby_between_basement_and_upstairs():
    """The castle interior is a line, not a clique — this two-hop walk is what
    breaks a plain def that assumed one area change."""
    assert path((6, 3), (6, 2)) == [(6, 3), (6, 1), (6, 2)]


# --- layer 2: simulation ---------------------------------------------------

def test_every_movement_completes_exactly_once_on_its_own_walk():
    for row in MOVEMENTS:
        events, level, area, _ = movement_walk(row)
        closed = run_engine(row, events, level, area)
        outcomes = [a.outcome for a in closed]
        assert outcomes == ["success"], (row["seed_key"], outcomes)


def test_no_movement_starts_and_ends_on_the_SAME_event():
    """A start and end that one event satisfies makes a def UNFIREABLE.

    SegmentEngine processes closures only for an ALREADY-armed def, so if the
    arming event is also the closing event the def arms and then hangs armed
    until something unrelated disarms it — no attempt is ever recorded. It
    happens when the world has a DIRECT edge from the source level to the
    destination: `level_exit from=A` and `level_enter to=B` are then the same
    level_changed. Live report 2026-07-24: DDD -> BitFS via the sub (the 23 ->
    19 one-way edge) armed on a warp out of DDD and showed as running in a
    completely different course.

    The walk-based simulation cannot catch this on its own — it always exits a
    course to its castle landing node, so it produced two events where the real
    sub hop produces one."""
    for row in MOVEMENTS:
        start, end = row["start_triggers"][0], row["end_triggers"][0]
        if start["type"] != "level_exit" or end["type"] != "level_enter":
            continue
        direct = [dst for dst in GRAPH.get((start["from"], None), [])
                  if dst[0] == end["to"]]
        assert not direct, (
            row["seed_key"],
            f"level_exit from={start['from']} and level_enter to={end['to']} "
            "are one event — the world has a direct edge; start it earlier")


def test_a_menu_warp_into_a_course_arms_no_movement():
    """The whole class behind the live report of 2026-07-27.

    Usamune's warp menu fabricates edges the world does not have: warping
    between two stars is ONE level_changed straight from course to course, so
    `level_exit from=24` fires with Mario standing in Cool, Cool Mountain. No
    castle movement can be RUN from inside a course — it needs the castle to
    walk through — so the armed set, which is what the practice page pins and
    labels ACTIVE SEGMENT, must stay empty however the player got there.

    Every source level any movement exits, crossed with every course in the
    game: 24 -> 5 is the reported case, the rest are the same bug waiting.
    Note the destination's own movement is no exception — warping WF -> CCM
    did not PERFORM "WF -> CCM", and arming it there would hang forever (the
    start and end are one event; see the UNFIREABLE test above)."""
    engine = SegmentEngine([
        SegmentDef(id=index, name=row["name"], enabled=True,
                   start_triggers=row["start_triggers"],
                   end_triggers=row["end_triggers"],
                   waypoints=row["waypoints"], guards=[],
                   match_mode=row.get("match_mode", "strict"))
        for index, row in enumerate(MOVEMENTS, start=1)])
    sources = sorted({row["start_triggers"][0]["from"] for row in MOVEMENTS
                      if row["start_triggers"][0]["type"] == "level_exit"})
    frame = 100
    for source in sources:
        for destination in sorted(COURSE_BY_LEVEL):
            if destination == source:
                continue
            frame += 1
            engine.feed(
                Ev(frame, "level_changed", frame,
                   {"from": source, "to": destination}),
                MatchContext(level=destination, prev_level=source, num_stars=0,
                             area=1))
            armed = sorted(engine.armed_ids())
            assert not armed, (
                f"warp {source} -> {destination} armed "
                f"{[MOVEMENTS[i - 1]['seed_key'] for i in armed]}")


def test_a_movement_only_fires_on_a_walk_that_reaches_its_endpoint():
    """Negative pass over all 56x55 pairs. A movement may legitimately
    complete on another's walk when it is a PREFIX of it — walking BBH -> DDD
    really does pass through the basement, so BBH -> Basement completing there
    is correct, not a false positive. What must never happen is completing on
    a walk that never reaches this movement's endpoint at all."""
    for row in MOVEMENTS:
        endpoint = clause_node(row["end_triggers"][0])
        for other in MOVEMENTS:
            if other["seed_key"] == row["seed_key"]:
                continue
            events, level, area, nodes = movement_walk(other)
            closed = run_engine(row, events, level, area)
            fired = [a for a in closed if a.outcome == "success"]
            if endpoint in nodes:
                continue          # the player genuinely passed through it
            assert not fired, (row["seed_key"], "wrongly completed on",
                               other["seed_key"])


# --- layer 3: Task 20's four real walks (spec 2026-07-28-multi-step-segments)
# These four shapes do not fit movement_walk's generic BFS-shortest-path
# model: two of them (100c->exit, reds->pipe) start or end on a
# star_grabbed/warp_entered clause clause_node cannot resolve, and all four
# replay a SPECIFIC real walk from a task report rather than the shortest
# one. Hand-built via _Walker directly instead of movement_walk.

def _seg(seed_key):
    return next(s for s in SEGMENTS if s["seed_key"] == seed_key)


def test_bowser_1_to_wf_IS_the_bitdw_reentry_detour():
    """Walk: "Bowser 1 -> re-enter BitDW -> pause-exit to Lobby -> WF."

    THIS BELIEF HAS NOW REVERSED TWICE, and the pair of reversals is worth
    keeping whole because the second one is a premise error rather than a
    change of mind.

    v1 asserted the detour SURVIVED (a loose def is transparent between its
    start and its end). v2, 2026-08-02, asserted it was VOIDED, on Griffin's
    strictness ruling -- *"That is a fixed path, and there are no other
    options... There are no deviations that are allowed"* -- plus a reason
    that turned out to be false: "the detour buys nothing here, the arena exit
    already lands in the Lobby, so re-entering BitDW is a wrong turn, not a
    trick."

    v3, 2026-08-03, from live play: it is a trick, and it is HIS route. He
    watched the card vanish mid-run and reported it -- "It displays when I exit
    Bowser 1, but when I enter Bowser in the Dark World (to exit to lobby),
    it's no longer displayed." Asked whether the detour IS the route or merely
    one of two, he picked IS.

    So the strictness ruling was never wrong; the ROUTE written under it was.
    That is the whole argument for the Then editor and the recorder shipped the
    same day: a declared path is only as good as the person declaring it, and
    he can now fix one himself instead of reporting it.

    The direct route records NOTHING now, deliberately -- same trade the
    `Bowser 2 -> Upstairs` row makes one arena later, and the test below it
    pins that half."""
    row = _seg("seg:bowser1->wf")
    origin = (30, None)
    walker = _Walker(origin)
    walker.hop(exit_node(30))    # Bowser 1 exit -> lobby
    walker.hop((17, None))       # re-enter BitDW -- step 1, declared
    walker.hop((6, 1))           # pause-exit BitDW -> lobby -- step 2
    walker.hop((24, None))       # walk into WF -- the end
    closed = run_engine(row, walker.events, origin[0], origin[1])
    assert [a.outcome for a in closed] == ["success"], (row["seed_key"], closed)


def test_bowser_1_to_wf_records_nothing_run_the_direct_way():
    """The cost of the ruling above, pinned rather than left implicit: with
    the BitDW re-entry declared as a required step, walking straight from the
    Lobby into WF is no longer this movement. Silent, as every off-route
    cancel in this engine is -- a movement that never happened must not bank a
    failure either. If he ever wants BOTH to count, that is a second
    definition beside this one, not a loosening of this one."""
    row = _seg("seg:bowser1->wf")
    origin = (30, None)
    walker = _Walker(origin)
    walker.hop(exit_node(30))    # Bowser 1 exit -> lobby
    walker.hop((24, None))       # straight into WF, skipping the declared BitDW
    closed = run_engine(row, walker.events, origin[0], origin[1])
    assert closed == [], (row["seed_key"], closed)


def test_bowser_2_to_bits_survives_the_whole_detour():
    # Task 0017: finish Bowser 2 -> back into BitFS -> pause exit -> upstairs
    # -> BLJs -> BitS. The route is a TRICK and is now DECLARED step by step
    # rather than tolerated: under loose matching every one of these was
    # merely transparent, which is the same thing the engine did for a wrong
    # turn.
    #
    # The walk itself was also wrong about the game and is corrected here: a
    # pause exit lands in the LOBBY, never back in the basement you entered
    # from (Griffin, live-tested in every course 2026-08-02). Skipping the
    # Basement -> Lobby walk is the entire point of re-entering BitFS.
    row = _seg("seg:bowser2->bits")
    origin = (33, None)
    walker = _Walker(origin)
    walker.hop(exit_node(33))    # Bowser 2 exit -> basement
    walker.hop((19, None))       # re-enter BitFS
    walker.hop((6, 1))           # pause-exit BitFS -> LOBBY (skips the walk)
    walker.hop((6, 2))           # upstairs
    walker.hop((21, None))       # BLJ into BitS
    closed = run_engine(row, walker.events, origin[0], origin[1])
    outcomes = [a.outcome for a in closed]
    assert outcomes == ["success"], (row["seed_key"], outcomes)


def test_the_two_step_bowser2_upstairs_then_bits_entry_survives_the_same_detour():
    """Route regression fix (2026-07-28): Task 20 rewrote the 0/1-star
    routes' two-step "-> Upstairs" + "Endless Staircase BLJ" tail into a
    single seg:bowser2->bits movement, so the new movement would have a route
    referencing it. That silently dropped a named trick split ("Endless
    Staircase BLJ" has its own timing) from the 0/1-star categories, the most
    BLJ-heavy runs in the game — and it was never necessary.

    Feeds the IDENTICAL real walk test_bowser_2_to_bits_survives_the_whole_
    detour uses through the two ORIGINAL segments in one engine instead of
    the new one: seg:bowser2->upstairs (loose, converted Task 19) survives
    the whole detour — BitFS re-entry, pause exit to the basement, lobby —
    transparently, exactly like seg:bowser2->bits does, and closes on
    reaching Upstairs. seg:bits-entry (legacy, strict, unguarded) then arms
    on that SAME event ("closures before arming", module docstring) and
    survives the BLJ with nothing to disarm it, closing on the BitS entry.
    Both close, so the rewrite bought nothing but the lost split;
    _LOW_STAR_TAIL was reverted — see its comment in corpus_routes_main.py."""
    upstairs_row = _seg("seg:bowser2->upstairs")
    bits_entry_row = _seg("seg:bits-entry")
    defs = [
        SegmentDef(id=1, name=upstairs_row["name"], enabled=True,
                   start_triggers=upstairs_row["start_triggers"],
                   end_triggers=upstairs_row["end_triggers"],
                   waypoints=upstairs_row["waypoints"], guards=[],
                   match_mode=upstairs_row.get("match_mode", "strict")),
        SegmentDef(id=2, name=bits_entry_row["name"], enabled=True,
                   start_triggers=bits_entry_row["start_triggers"],
                   end_triggers=bits_entry_row["end_triggers"],
                   waypoints=bits_entry_row["waypoints"], guards=[],
                   match_mode=bits_entry_row.get("match_mode", "strict")),
    ]
    engine = SegmentEngine(defs)

    origin = (33, None)
    walker = _Walker(origin)
    walker.hop(exit_node(33))    # Bowser 2 exit -> basement
    walker.hop((19, None))       # re-enter BitFS
    walker.hop((6, 1))           # pause-exit BitFS -> LOBBY (skips the walk)
    walker.hop((6, 2))           # upstairs
    walker.hop((21, None))       # BLJ into BitS

    level, area = origin
    closed = []
    for ev in walker.events:
        prev_level = level
        if ev.type == "level_changed":
            level = ev.payload["to"]
        if ev.type == "area_changed":
            area = ev.payload["to"]
        ctx = MatchContext(level=level, prev_level=prev_level, num_stars=0,
                           area=area)
        got, _ = engine.feed(ev, ctx)
        closed.extend(got)

    outcomes = [(a.segment_id, a.outcome) for a in closed]
    # id 1 = seg:bowser2->upstairs closes first (at Upstairs); id 2 =
    # seg:bits-entry arms on that same event and closes second (at BitS).
    assert outcomes == [(1, "success"), (2, "success")], outcomes


def test_a_100_coin_star_segment_ends_on_the_star_that_exits_the_level():
    # Reshaped 2026-07-29 (live report): the segment now arms on STAGE ENTRY,
    # not the 100-coin grab itself, so it can be offered before the grab and
    # times the whole course visit. The 100-coin grab is a WAYPOINT; in a
    # normal stage it does not exit, the segment ends on a DIFFERENT, named
    # star.
    row = _seg("seg:100c->exit:wf")
    stage_entry = [
        Ev(1, "level_changed", 100, {"from": 6, "to": 24}),
    ]
    closed = run_engine(row, stage_entry, 6, None)
    assert closed == [], "stage entry alone must not exit the level"

    hundred_coins_only = stage_entry + [
        Ev(2, "star_collected", 150,
           {"course_id": 2, "star_id": 6, "num_stars": 0}),
    ]
    closed = run_engine(row, hundred_coins_only, 6, None)
    assert closed == [], "the 100-coin grab alone must not exit the level"

    events = hundred_coins_only + [
        Ev(3, "star_collected", 250,
           {"course_id": 2, "star_id": 2, "num_stars": 1}),
    ]
    closed = run_engine(row, events, 6, None)
    outcomes = [a.outcome for a in closed]
    assert outcomes == ["success"], (row["seed_key"], outcomes)


def test_hundred_coin_exit_count_and_shape():
    """Structural guard, mutation-provable: 15 rows (one per main course),
    each arming on that course's stage entry, waypointing through its
    100-coin star, and ending on any of its six OTHER stars -- never the
    100-coin star itself, and never unguarded into some other course's
    stars."""
    rows = [s for s in SEGMENTS if s["category"] == "100 Coin Exit"]
    assert len(rows) == 15
    seen_courses = set()
    for row in rows:
        starts = {t["type"] for t in row["start_triggers"]}
        assert starts == {"level_enter", "attempt_anchor"}
        levels = {t.get("to", t.get("level")) for t in row["start_triggers"]}
        assert len(levels) == 1, "start clauses disagree on which level"
        assert len(row["waypoints"]) == 1 and len(row["waypoints"][0]) == 1
        waypoint = row["waypoints"][0][0]
        assert waypoint["type"] == "star_grabbed" and waypoint["star"] == 6
        seen_courses.add(waypoint["course"])
        ends = row["end_triggers"]
        assert len(ends) == 6
        assert all(e["type"] == "star_grabbed"
                   and e["course"] == waypoint["course"]
                   and e["star"] != 6 for e in ends)
        assert row["guards"] == []            # no route ambiguity -- always on
        assert row["match_mode"] == "strict"
    assert seen_courses == set(range(1, 16))  # every main course, no gaps


def test_a_bowser_reds_segment_ends_on_the_pipe_not_the_star():
    # Reshaped 2026-07-29 (live report): the segment now arms on STAGE ENTRY,
    # not the reds-star grab itself, so it can be offered before the grab and
    # times the whole stage (previously it measured only star->pipe -- his
    # log showed 237/378-frame "successes"). The reference autosplitter's own
    # default (it waits for pipe entry), and the case its issue tracker shows
    # it repeatedly getting wrong.
    row = _seg("seg:reds->pipe:bitdw")
    stage_entry = [
        Ev(1, "level_changed", 100, {"from": 16, "to": 17}),
    ]
    closed = run_engine(row, stage_entry, 16, None)
    assert closed == [], "stage entry alone must not finish the level"

    reds_only = stage_entry + [
        Ev(2, "star_collected", 150,
           {"course_id": 16, "star_id": 0, "num_stars": 0}),
    ]
    closed = run_engine(row, reds_only, 16, None)
    assert closed == [], "the reds star alone must not finish the level"

    events = reds_only + [
        Ev(3, "warp_entered", 250, {"level": 17}),
    ]
    closed = run_engine(row, events, 16, None)
    outcomes = [a.outcome for a in closed]
    assert outcomes == ["success"], (row["seed_key"], outcomes)


def test_reds_to_pipe_count_and_shape():
    rows = [s for s in SEGMENTS if s["seed_key"].startswith("seg:reds->pipe:")]
    assert len(rows) == 3
    for row in rows:
        starts = {t["type"] for t in row["start_triggers"]}
        assert starts == {"level_enter", "attempt_anchor"}
        levels = {t.get("to", t.get("level")) for t in row["start_triggers"]}
        assert len(levels) == 1, "start clauses disagree on which level"
        assert len(row["waypoints"]) == 1 and len(row["waypoints"][0]) == 1
        waypoint = row["waypoints"][0][0]
        assert waypoint["type"] == "star_grabbed" and waypoint["star"] == 0
        assert row["end_triggers"][0]["type"] == "warp_entered"
        assert row["guards"] == []
        assert row["category"] == "Castle Movement"
        assert row["match_mode"] == "strict"


def test_a_slow_hundred_coin_run_no_longer_has_a_staleness_budget():
    """SUPERSEDES the loose-mode limitation this test used to document
    (2026-07-29): replaying the user's real 659-event session found the
    ONLY genuine 100c->exit completion in the whole journal (WF, three
    practice_resets before ever grabbing the 100-coin star, 185.4s total)
    silently swallowed by the loose-mode staleness deadline
    (segments.py::budget_frames -- MIN_BUDGET_FRAMES, measured against
    castle MOVEMENTS' much shorter completions, not 100-coin hunts).

    The SAME-DAY match_mode fix (loose -> strict, live report: a segment
    stayed "running" after the player left the course) removes this as a
    side effect: `_deadline_for` (segments.py) returns None for any
    non-loose def, so a strict/waypoint dispatch never computes a budget at
    all. This test now proves the opposite of what it used to: a completion
    taking far longer than the OLD floor still records success, because
    there is no floor to exceed any more. `budget_frames(None)` is still the
    reference point (not a literal frame count) so this stays meaningful if
    a future def in this family ever reverts to loose."""
    row = _seg("seg:100c->exit:wf")
    floor = budget_frames(None)
    events = [
        Ev(1, "level_changed", 100, {"from": 6, "to": 24}),
        Ev(2, "star_collected", 100 + floor + 3000,   # well past the old floor
           {"course_id": 2, "star_id": 6, "num_stars": 0}),
        Ev(3, "star_collected", 100 + floor + 6000,
           {"course_id": 2, "star_id": 2, "num_stars": 1}),
    ]
    closed = run_engine(row, events, 6, None)
    assert [a.outcome for a in closed] == ["success"], (
        "a strict/waypoint def has no staleness deadline -- this must "
        "succeed regardless of how long the course visit actually took")


def test_leaving_the_course_deactivates_the_hundred_coin_segment():
    """Live report (2026-07-29), the fix this whole mode change is for: he
    left DDD for BitFS mid-visit and "DDD -- 100 Coins -> Exit" still read
    RUNNING -- loose is transparent to level_changed by design, so it never
    disarmed. Strict routes to _feed_waypoint (segments.py), whose
    major-action cancel treats a real-edge level_changed that isn't the next
    waypoint as "the player switched tasks or misrouted" -- exactly
    "deactivate when I leave the stage". No row, no success: a silent
    cancel, same as any other abandoned attempt.

    Checks `engine.armed_ids()`, not just the closed-attempts list: a silent
    cancel and "still armed, nothing has closed YET" both produce an empty
    `closed` list, so asserting on `closed` alone cannot tell the fixed
    behaviour apart from the exact bug being fixed here -- the segment must
    actually leave the armed set."""
    row = _seg("seg:100c->exit:ddd")
    d = SegmentDef(id=1, name=row["name"], enabled=True,
                   start_triggers=row["start_triggers"],
                   end_triggers=row["end_triggers"],
                   waypoints=row["waypoints"], guards=[],
                   match_mode=row["match_mode"])
    engine = SegmentEngine([d])
    events = [
        Ev(1, "level_changed", 100, {"from": 6, "to": 23}),   # enter DDD
        Ev(2, "level_changed", 150, {"from": 23, "to": 19}),  # leave for BitFS
    ]
    level = 6
    for ev in events:
        prev_level = level
        level = ev.payload["to"]
        ctx = MatchContext(level=level, prev_level=prev_level, num_stars=0, area=None)
        closed, _ = engine.feed(ev, ctx)
        assert closed == []
    assert engine.armed_ids() == set(), (
        "leaving the course must disarm the segment -- it must not still "
        "read as running")


def test_a_courses_own_subarea_stays_transparent_mid_hundred_coin_run():
    """The other half of the same fix, checked so the level-change cancel
    above doesn't overcorrect: a course's OWN subareas (DDD's submarine bay
    is the live-reported example) must NOT cancel a visit that legitimately
    crosses them -- `_feed_waypoint` treats `area_changed` as transparent
    (it is not in `_MAJOR_EVENT_TYPES` and is not a `level_changed`), so
    moving between subareas of the SAME course changes nothing, and the run
    can still complete afterward."""
    row = _seg("seg:100c->exit:ddd")
    events = [
        Ev(1, "level_changed", 100, {"from": 6, "to": 23}),      # enter DDD
        Ev(2, "area_changed", 120, {"level": 23, "from": 1, "to": 2}),  # into the sub
        Ev(3, "star_collected", 150,
           {"course_id": 9, "star_id": 6, "num_stars": 0}),      # 100 coins
        Ev(4, "area_changed", 180, {"level": 23, "from": 2, "to": 1}),  # back out
        Ev(5, "star_collected", 250,
           {"course_id": 9, "star_id": 0, "num_stars": 1}),      # exit star
    ]
    closed = run_engine(row, events, 6, None)
    assert [a.outcome for a in closed] == ["success"], (
        "area_changed within the same course must stay transparent")


def test_grabbing_an_ordinary_star_first_cancels_the_hundred_coin_attempt():
    """The consequence the brief asked to have confirmed rather than
    assumed: `_feed_waypoint`'s major-action cancel fires on ANY
    star_collected that isn't the next expected waypoint, so grabbing an
    ordinary star (not the 100-coin one) before the 100-coin grab ends the
    attempt -- silently, no row.

    This is CORRECT, not a false positive, checked against actual SM64
    behaviour rather than assumed: grabbing any star except the 100-coin one
    triggers the star-grab cutscene and returns Mario to the castle -- the
    very asymmetry this family exists to express ("you don't exit the stage
    when you grab a 100 coins star... you must find another star to
    actually exit the level"). There is no real sequence where an ordinary
    star is grabbed and the player keeps playing in the same course visit,
    so cancelling here is not stricter than reality, it is reality arriving
    slightly before the level_changed that would have cancelled it anyway.

    Checks `armed_ids()`, not just `closed` -- under the OLD "loose" mode
    this same sequence ALSO produces an empty `closed` list (loose has no
    major-action cancel branch at all, so it stays transparently armed
    instead), so `closed == []` alone cannot tell "cancelled" apart from
    "still running and just hasn't ended yet"."""
    row = _seg("seg:100c->exit:wf")
    d = SegmentDef(id=1, name=row["name"], enabled=True,
                   start_triggers=row["start_triggers"],
                   end_triggers=row["end_triggers"],
                   waypoints=row["waypoints"], guards=[],
                   match_mode=row["match_mode"])
    engine = SegmentEngine([d])
    events = [
        Ev(1, "level_changed", 100, {"from": 6, "to": 24}),
        Ev(2, "star_collected", 150,
           {"course_id": 2, "star_id": 2, "num_stars": 0}),  # an ORDINARY star
    ]
    level = 6
    for ev in events:
        prev_level = level
        if ev.type == "level_changed":
            level = ev.payload["to"]
        ctx = MatchContext(level=level, prev_level=prev_level, num_stars=0, area=None)
        closed, _ = engine.feed(ev, ctx)
        assert closed == [], "an ordinary star grab before the 100-coin one must cancel, no row"
    assert engine.armed_ids() == set(), (
        "an ordinary star grab must actually cancel the attempt (leave the "
        "armed set), not merely fail to close it yet")


# --- lint gate (Task 15, tracking/lint.py) ----------------------------------

def seeded_definitions() -> list:
    """Every seeded segment as a real SegmentDef, id-stable within one call
    (1-based index into SEGMENTS, matching the id scheme every other builder
    in this file uses) -- the shape tracking/lint.py's lint_definition reads,
    for the corpus-clean gate below."""
    return [
        SegmentDef(id=index, name=row["name"], enabled=row["enabled"],
                   start_triggers=row["start_triggers"],
                   end_triggers=row["end_triggers"],
                   waypoints=row["waypoints"], guards=row["guards"],
                   match_mode=row.get("match_mode", "strict"))
        for index, row in enumerate(SEGMENTS, start=1)]


def test_no_seeded_definition_trips_a_known_trap():
    """These four traps (tracking/lint.py's module docstring carries each
    one's live report) each cost a live report already. The corpus must
    never regress into one -- error severity only: a warning
    (start_looser_than_waypoint, duplicate) is advisory, not a broken def,
    and this is a behavioral gate, not a style one."""
    defs = seeded_definitions()
    for d in defs:
        errors = [w for w in lint_definition(d, defs) if w["severity"] == "error"]
        assert errors == [], (d.name, errors)
