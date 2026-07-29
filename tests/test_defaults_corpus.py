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
from sm64_events.memory.addresses import (COURSE_BY_LEVEL,
                                          LEVEL_CASTLE_INSIDE,
                                          WORLD_EDGES_ONE_WAY,
                                          WORLD_EDGES_TWO_WAY)
from sm64_events.tracking.segments import (MatchContext, SegmentDef,
                                           SegmentEngine, validate_definition)

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
    grounds, CotMC -> HMC). Bowser arenas have only a one-way exit edge."""
    neighbours = GRAPH.get((level, None), [])
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
                self._add("level_changed", {"from": self.at[0], "to": nxt[0]})
                self.at = nxt
                if nxt[0] == LEVEL_CASTLE_INSIDE:
                    # detectors/area.py establishes the destination area on
                    # the SAME tick (from == to: bookkeeping, not a crossing)
                    self._add("area_changed",
                              {"level": LEVEL_CASTLE_INSIDE, "from": nxt[1],
                               "to": nxt[1], "from_transient": True},
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
    left at SegmentDef's "strict" default -- since all 55 movements now ship
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
    assert len(MOVEMENTS) == 55
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
    assert exit_node(34) == (6, 2)     # Bowser 3 arena -> upstairs


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
    """Negative pass over all 55x54 pairs. A movement may legitimately
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
