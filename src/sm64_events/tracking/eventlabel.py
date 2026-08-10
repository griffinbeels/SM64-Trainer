# src/sm64_events/tracking/eventlabel.py
"""One journal event -> one sentence a human can point at (spec
2026-07-28-multi-step-segments, Task 10). This is the first piece of the
feature the user actually asked for: define a segment by pointing at what
you just did, instead of hand-authoring trigger clauses. `label_event(row)`
turns a single `EventRow` (storage/db.py) into that sentence, or `None` when
the event is not something a human would ever pick as a segment boundary —
the timeline (a later task) filters `None` rows out entirely rather than
rendering them.

The journal carries 30+ distinct event types and most of them are per-tick
telemetry (`rollout`, `jump`, `mario_acted` — fired on every first non-passive
action, carrying only the inert action id) or derived bookkeeping (`attempt_completed`,
`target_changed`, `session_started`) that nobody would ever call "the step I
just did". `label_event` only recognizes the types this module's sibling
registry (`tracking/segments.py::TRIGGERS`) can actually turn into a segment
clause — see `test_every_trigger_type_has_a_labellable_event_shape` in
test_eventlabel.py, which fails the day a new `TriggerType` lands whose
underlying journal event this module has no rule for (a trigger the timeline
cannot produce is a segment the user cannot record).

`ui/components/feed.js` stays a raw diagnostics view over the same journal
and is DELIBERATELY NOT migrated onto this module. Showing every raw event
including the ones this module calls noise is exactly feed.js's job as a
developer diagnostic; a segment-boundary picker has the opposite job.

THE ID-SPACE TRAP (measured against the shipped tables, ledger 2026-07-28
— read this before touching any formatter below). A *level* id and a
*course* id are DIFFERENT NUMBERING SYSTEMS that happen to agree at id 15
(LEVEL_NAMES[15] == COURSE_NAMES[15] == "Rainbow Ride") and disagree
everywhere else that matters — e.g. LEVEL_NAMES[9] is "Bob-omb Battlefield"
but COURSE_NAMES[9] is "Dire, Dire Docks". Swapping the tables produces a
PLAUSIBLE WRONG PLACE NAME, never a crash and never an empty string, so a
spot check on Rainbow Ride (id 15) would pass either way.
`level_changed`/`area_changed`/`warp_entered`/`spawned`/`key_grabbed` all
carry LEVEL ids (`detectors/level.py`, `area.py`, `warp.py`, `spawn.py`,
`key.py`) and are named ONLY through `LEVEL_NAMES`/`CASTLE_AREA_NAMES`.
`star_collected` carries a COURSE id and is named ONLY through the canonical
`course_name(course_id)`/`star_name(course_id, star_id)` accessors — never by
indexing `COURSE_NAMES`/`STAR_NAMES` directly (`STAR_NAMES[course]` is a
TUPLE positioned by star_id, not a dict; this has already cost two wrong
assumptions elsewhere in the codebase). `test_level_id_and_course_id_
are_different_number_spaces` pins id 9, where the two tables disagree, so a
table swap fails loudly rather than merely being wrong on a place nobody
spot-checks.

THE LABELLING-VOLUME DECISION (the judgement call this task's brief
deliberately left open, quantified against the real journal — 18,656 events,
2026-07-28). Of the 9 journal event types this module CAN label, three
dominate by raw volume: `practice_reset` (2,829), `area_changed` (1,678),
`spawned` (1,164) — 76% of the "capable" set. The four types a human would
call "a step I just performed" total only 1,780 (`level_changed` 925 +
`star_collected` 635 + `warp_entered` 199 + `key_grabbed` 21).

This module labels ALL NINE anyway, regardless of volume — DECISION (b) from
the brief's three sketched shapes, "label everything and let the timeline's
default view narrow it". The alternative of dropping the high-volume types
here was never viable: `practice_reset`/`state_loaded` back the seeded LBLJ
definition's `attempt_anchor` start trigger, and `area_changed`/`spawned`
back real `area_enter`/`spawned` start triggers in the same corpus — making
any of them unlabellable would make an existing definition's start
un-recordable through this feature, which is a correctness regression dressed
up as a display preference. `label_event`'s job is "CAN this be a boundary",
not "should the default view show it" — that is a volume/recency/grouping
question for whichever later task builds the timeline endpoint (candidate
shapes already on the table: a recency cap, a step-subset default with the
rest reachable, or `practice_reset` rendered as a separator rather than a
pickable row). Baking a volume opinion into this pure function would make
that later decision harder to change, not easier, and none of the three
candidate shapes need this module to withhold a label — they all operate on
the labelled STREAM, deciding what to show, not what CAN be shown.
"""
from collections.abc import Callable

from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.anchors import PAUSE_WARP_MIN_STREAK
from sm64_events.detectors.counter_epoch import (COURSE_START_AREA,
                                                 LEVEL_LOAD_TAIL_FRAMES)
from sm64_events.detectors.moment import MOMENTS
from sm64_events.memory.addresses import (CASTLE_AREA_NAMES, LEVEL_CASTLE_INSIDE,
                                          LEVEL_NAMES, course_name, star_name,
                                          subarea_name, warp_word)


def _level_name(level_id) -> str:
    return LEVEL_NAMES.get(level_id, f"Level {level_id}")


def _area_name(area_id) -> str:
    return CASTLE_AREA_NAMES.get(area_id, f"Area {area_id}")


def _level_changed(payload: dict) -> str | None:
    from_id, to_id = payload.get("from"), payload.get("to")
    if from_id is None or to_id is None or from_id == to_id:
        # from == to is an establishing/corrective bookkeeping event
        # (detectors/level.py) — never real movement, never a boundary.
        return None
    return f"Exited {_level_name(from_id)} into {_level_name(to_id)}"


def _area_changed(payload: dict) -> str | None:
    """"Moved into the Basement", or — outside the castle — "Moved to another
    part of Lethal Lava Land".

    A SUBAREA ONLY MEANS SOMETHING INSIDE THE CASTLE INTERIOR, the same rule
    `tracking/topology.py::node_for` states and for the same reason: courses
    have their own areas (SSL area 2 is the pyramid interior, LLL area 2 the
    volcano) and nothing names them. This used to read `CASTLE_AREA_NAMES`
    unconditionally, so an area change inside a course announced a castle room
    it had nothing to do with — "Moved into the Lobby" while standing in Bowser
    in the Fire Sea (2026-08-05, seen the moment the recorder started drawing
    one card per place and the row landed inside the card that contradicted
    it). Harmless while nothing put the two side by side; a row asserting the
    wrong place, next to a heading naming the right one, reads as the tool
    being broken.
    """
    from_area, to_area = payload.get("from"), payload.get("to")
    if to_area is None or from_area == to_area:
        # same reasoning as _level_changed: from == to is bookkeeping.
        return None
    level = payload.get("level")
    if level == LEVEL_CASTLE_INSIDE:
        return f"Moved into the {_area_name(to_area)}"
    if level is None:
        # A legacy row with no level cannot be placed either way. Say what is
        # known rather than guessing a castle room.
        return f"Moved into area {to_area}"
    return f"Moved to another part of {_level_name(level)}"


def _star_collected(payload: dict) -> str | None:
    # course_id/star_id, not the payload's own course_name/star_name strings:
    # this reads through the SAME canonical accessors detectors/star_grab.py
    # used to produce those strings in the first place, so it can never
    # disagree with a well-formed payload, and it degrades gracefully
    # (star_name(99, 99) -> "Star 100") rather than depending on a field a
    # legacy journal might not carry.
    course_id, star_id = payload.get("course_id"), payload.get("star_id")
    if course_id is None or star_id is None:
        return None
    return f"Grabbed {star_name(course_id, star_id)} in {course_name(course_id)}"


def entrance_key(payload: dict) -> str:
    """The identity an ENTRANCE row renames — the entrance ITSELF, derived
    from where it stands and where it leads, never the payload's landmark.

    The payload landmark on an entrance row is whatever object Mario last
    engaged: a painting is level geometry and never touches the pointer, so
    his three CCM entries carried the lobby DOOR's key and the rename pencil
    edited the door (round 12 items 5+6, journal ids 3908/3933/3943 —
    *"If I'm setting the name for a specific row, I would expect THAT row to
    change"*). Deriving the key from (level, area, to) works for every
    historical row too, which no detector fix could reach."""
    area = payload.get("area")
    return (f"entrance:{payload.get('level')}"
            f":{area if area is not None else 0}:{payload.get('to')}")


def entrance_landmark(payload: dict) -> dict:
    """The landmark fields a timeline ENTRANCE row serves in place of its
    payload's — shaped like `core/landmark.py::Landmark.payload()` where it
    matters to the client (key + nameable drive the rename pencil)."""
    return {"key": entrance_key(payload), "kind_key": None,
            "placed": True, "nameable": True}


def _warp_entered(payload: dict, names: dict) -> str | None:
    """Three different things wear this one event type, and the row has to
    say WHICH -- a player reading his own history recognises the thing he
    touched, not the event we named it after.

    Live report 2026-08-05, hunting his own BOB warp in the recorder's list:
    every row read "Entered the pipe", so *"I don't see anything about warps
    here? I guess it's 'Entered the pipe in Bob-omb Battlefield'?"* -- correct,
    and unrecognisable. `warp_word` carries his own rule (pipe in a Bowser
    stage, warp everywhere else); a course ENTRANCE is the most specific case
    and is named by where it leads rather than by what it looks like.

    AN ENTRANCE LEAVES THE LEVEL, and reading `to` alone said otherwise for a
    day. Live report 2026-08-06: *"'Touched the Bob-omb Battlefield entrance in
    Bob-omb Battlefield' is *really* actually a warp. This is warp detection."*
    Three recent rows read `to == level` -- ids 2330 and 2327 inside BoB, 2306
    and 2288 inside LLL -- and every one of them is an INTRA-COURSE warp, which
    never leaves the course and so has no entrance to name. `to != level` is
    the whole test, and it is in the payload already.

    WHICH warp, once he has named it, comes from the same catalogue a moment
    reads: the landmark work reached moments and never reached warps, so his
    two BoB warps were literally the same sentence twice and neither could be
    renamed. Named and unnamed differ only in the noun, exactly as a moment's
    do -- and a KIND name ("warp pipe") is the cheaper level, covering every
    instance in the game at once.
    """
    level = payload.get("level")
    if level is None:
        return None
    destination = payload.get("to")
    if destination is not None and destination != level:
        # An entrance's own name comes from the catalogue under its DERIVED
        # key (see entrance_key above), never the payload landmark's — that
        # one belongs to whatever he touched last.
        named = names.get(entrance_key(payload))
        if named:
            return f"Touched {named} in {_level_name(level)}"
        return (f"Touched the {_level_name(destination)} entrance in "
                f"{_level_name(level)}")
    named, kind_named = _landmark_names(payload, names)
    if named:
        return f"Entered the {named} in {_level_name(level)}"
    if kind_named:
        return f"Entered {_kind_phrase(kind_named)} in {_level_name(level)}"
    return f"Entered a {warp_word(level)} in {_level_name(level)}"


# detectors/key.py's FIGHT_END_LEVELS values -> the human-facing object of
# "Grabbed ___ in ___". An unrecognised `which` (should never happen — this
# set mirrors FIGHT_END_LEVELS exactly) falls back to a generic phrase rather
# than raising, matching this module's degrade-gracefully convention.
_KEY_WHICH_LABELS = {
    "bitdw": "the Bowser 1 key",
    "bitfs": "the Bowser 2 key",
    "grand": "the Grand Star",
}


def _key_grabbed(payload: dict) -> str | None:
    level, which = payload.get("level"), payload.get("which")
    if level is None or which is None:
        return None
    return f"Grabbed {_KEY_WHICH_LABELS.get(which, 'a key')} in {_level_name(level)}"


_MOMENT_LABELS = {m.kind: m.label for m in MOMENTS}

# The labellers that take (payload, names) instead of (payload).
_READS_THE_CATALOGUE = frozenset({"moment_reached", "warp_entered"})


def _landmark_names(payload: dict, names: dict) -> tuple[str | None, str | None]:
    """(instance name, kind name) for the landmark this row carries.

    THE one reading of a landmark out of a payload — every labeller in
    `_READS_THE_CATALOGUE` goes through it, so "how a row finds its name in
    the catalogue" cannot fork per event type. The two-level scheme is the
    catalogue's own: an instance name ("HMC Door") beats a kind name ("door"),
    and a kind name covers every instance in the game at once."""
    landmark = payload.get("landmark") or {}
    return names.get(landmark.get("key")), names.get(landmark.get("kind_key"))


def _kind_phrase(name: str) -> str:
    """A kind name as it sits mid-sentence: "a bob-omb", "an exclamation box",
    "Bowser".

    The catalogue's case convention IS the grammar (corpus_behaviors.py ships
    it): a common noun is seeded lowercase and gets an article, a proper noun
    is seeded capitalized and stands bare — "Pick up a bob-omb" but "Pick up
    Bowser", never "a Bowser". Derived from the name's own first letter, so
    there is no second table to keep in step with the catalogue."""
    if name[:1].islower():
        article = "an" if name[0] in "aeiou" else "a"
        return f"{article} {name}"
    return name


def _moment_reached(payload: dict, names: dict) -> str | None:
    """"Open the HMC Door in Castle Inside" once he has named that door, and
    "Open a door in Big Boo's Haunt" from the shipped kind catalogue.

    A NAMED LANDMARK DROPS THE ORDINAL — his ruling 2026-08-05: *"it's less
    about the specific order of doors, and more about WHICH door is being
    entered"* — and since round 8 item 2 a named KIND drops it too: the
    catalogue ships every behavior in the ROM named (corpus_behaviors.py), so
    keeping the ordinal on kind-named rows would have put two number formats
    on almost every row ("Pick up an object (#5)" beside the repeat counter's
    "(3)", his screenshot). The repeat counter is the one number left; the
    ordinal survives only on a row naming nothing at all, where it is still
    the only thing distinguishing the fifth from the fourth.

    A KIND name (`kind:800ebc8c` -> "door") replaces the moment's generic
    word for every instance in the game at once; grammar comes from the
    name's own case (`_kind_phrase`).
    """
    kind = payload.get("kind")
    if kind is None:
        return None
    what = _MOMENT_LABELS.get(kind, kind)
    where = _level_name(payload.get("level"))
    named, kind_named = _landmark_names(payload, names)
    if named:
        return f"{_verb(what)} the {named} in {where}"
    if kind_named:
        return f"{_verb(what)} {_kind_phrase(kind_named)} in {where}"
    ordinal = payload.get("ordinal")
    if ordinal and ordinal > 1:
        return f"{what} (#{ordinal}) in {where}"
    return f"{what} in {where}"


def _verb(label: str) -> str:
    """"Open a door" -> "Open". The registry's label is a whole phrase and a
    named landmark supplies its own noun, so the verb is what survives.

    " an " is here for "Pick up an object", and it is not a special case: any
    registry label naming its object needs its article stripped, and missing
    one produces "Pick up an object the Bob-omb", which reads as the tool being
    broken rather than as a missing rule."""
    for article in (" a ", " an ", " the "):
        label = label.split(article)[0]
    return label


def _spawn_place(level, payload: dict) -> str:
    """Where a spawn put Mario, at the finest grain the row can say —
    "Lethal Lava Land: Volcano" for a subarea spawn (round 20 item 3: "we
    should actually have a special 'Spawned into Lethal Lava Land [Subarea
    Name]' event"), the plain level name otherwise (including every
    historical row, which carries no area)."""
    sub = subarea_name(level, payload.get("area"))
    return f"{_level_name(level)}: {sub}" if sub else _level_name(level)


def _spawned(payload: dict) -> str | None:
    level = payload.get("level")
    if level is None:
        return None
    if payload.get("kind") == "intro":
        return f"Started the file in {_level_name(level)}"
    return f"Spawned into {_spawn_place(level, payload)}"


def label_level_entry(level, payload: dict | None = None) -> str:
    """The one sentence for ARRIVING somewhere — see `level_entry_rows`.

    A restart INSIDE a subarea is "different than spawning into the starting
    position of the entire course" (round 20 item 3), so a promoted spawn
    whose area is a named subarea says where it actually put him instead of
    claiming the whole level started over."""
    sub = subarea_name(level, (payload or {}).get("area"))
    if sub:
        return f"Spawned into {_level_name(level)}: {sub}"
    return f"Started {_level_name(level)}"


def level_entry_rows(events) -> tuple[set[int], dict[int, int]]:
    """Which rows a LEVEL LOAD produced, and which one row should speak for it.

    Returns `(settled_by_the_load, {spawn row id: level})`.

    THE REPORT, 2026-08-06: *"there are two spawn in events in SSL ('Moved to
    another part of Shifting Sand Land' is the spawn in event when it's at the
    beginning of the area like that). We should figure out how to explicitly
    detect spawning in as an event (starting the course)"* — and the same
    complaint one round earlier for Whomp's Fortress and Snowman's Land.

    He is reading the LOAD, and the load really does walk the area byte.
    Entering SSL, journal ids 2204-2209 inside two seconds:

        level_changed 24 -> 8
        area_changed  8: 1 -> 1     (unlabelled: from == to)
        area_changed  8: 1 -> 2     "Moved to another part of Shifting Sand Land"
        area_changed  8: 2 -> 1     "Moved to another part of Shifting Sand Land"
        practice_reset
        spawned {level: 8, kind: "spawn"}

    So the event he asked for ALREADY EXISTS and was the one row not shown:
    `detectors/spawn.py` fires on that last line, and the recorder's default
    view lists a `spawned` only for `kind == "intro"`. Two moves, one rule.

    THE WINDOW IS THE MEASURED ONE, not a new constant:
    `counter_epoch.LEVEL_LOAD_TAIL_FRAMES`, 60, derived across 911 level
    entries where the load's LAST area edge lands at 44-47 frames and the
    earliest genuine warp deeper into a level appears at 60. Reusing it is the
    point — the clock and the recorder must not disagree about where a load
    ends.

    A LOAD CONTRIBUTES EXACTLY ONE ROW — its spawn if it journaled one, and
    otherwise its LAST area edge, which is where the load left him. The second
    half is not defensive tidiness: journal ids 2172-2178 are a level entry
    that never reached a spawn, because he walked back out inside the tail, and
    a rule that only ever promoted spawns would have deleted that arrival from
    the list entirely.

    AN ARRIVAL IS A SPAWN, NOT A LEVEL EDGE — the correction, 2026-08-06, and
    the level edge was the convenient signal rather than the thing. He warped
    from Shifting Sand Land to Shifting Sand Land through the Usamune menu,
    which moves no level byte at all, so no load ever opened and the row he
    got was the load's area edge: *"I would expect it to show me the 'Started
    Shifting Sand Land'… I think when we're spawning, that's the event that
    should show"* (journal ids 2343-2345). A level edge still opens a load and
    still decides which rows the spawn SPEAKS FOR; it is no longer what makes
    an arrival an arrival.

    WHICH SPAWNS RESTART THE LEVEL, since most do not, and every clause here
    is a discriminator this project already measured rather than a new one:

    * the spawn must land in `COURSE_START_AREA` — a course always starts in
      area 1, so a spawn anywhere else is Mario going deeper (the pyramid, the
      volcano). `counter_epoch.is_area_load` turns on the same fact;
    * a spawn with NO area edge beside it is an L-reset in place — nothing
      moved but the clock, and that is a start (ids 2347, 2353, 2356);
    * a spawn WITH one is a warp, and the discriminator between the menu and
      his own feet is `anchors.PAUSE_WARP_MIN_STREAK` — walked loads pause
      0-3 frames, menu warps 13+. His two SSL warps read 80 and 1007 frames
      paused; walking into the pyramid and into the LLL volcano both read 3.

    That last clause is what stops "Started Lethal Lava Land" appearing every
    time he walks back out of the volcano, which the destination area alone
    cannot separate from a retry (`anchors.py` records the same limit for the
    attempt side of it).

    NOTHING IS DROPPED FROM THE JOURNAL. Every one of these rows is a true
    thing the game did, and `segments._zeroes_usamune_igt`, the topological
    matcher, `synthesize.walked_steps` and this endpoint's own position walk
    all read them. This says only which row a HUMAN should be offered to point
    at.
    """
    # THE TAIL IS SPENT IN UNPAUSED FRAMES (2026-08-07). `LEVEL_LOAD_TAIL_
    # FRAMES` was measured across 911 WALKED entries, where a load's last area
    # edge lands 44-47 frames after the level edge — but a Usamune MENU WARP
    # pauses INSIDE the load, and `frame` is the raw game counter, which runs
    # through a pause. His HMC -> LLL warp (journal ids 25830-25835) put 4,131
    # frames between the level edge and the load's own final area edge, with
    # the anchor's `paused_frames_before` reading 4,187. So the window expired
    # mid-load, the load closed having banked no spawn, and its no-spawn
    # branch PROMOTED one of the load's own area edges as the arrival: *"the
    # 'moved to another part of lethal lava land' … BEFORE the 'Started Lethal
    # Lava Land' is me warping from the previous stage. These are basically
    # garbage events, and reads as noise events before the 'start' event."*
    #
    # Anchors carry the pause and land co-frame with the rows being judged, so
    # the credit is collected in a PRE-PASS keyed by frame and spent as the
    # walk advances. Nothing else about the rule moves: a load still ends at
    # its spawn, at the next level edge, or at a tail that has genuinely
    # elapsed while the game was running.
    pause_at: dict[int, int] = {}
    for row in events:
        if row.type in ("practice_reset", "state_loaded"):
            paused = (row.payload or {}).get("paused_frames_before") or 0
            if paused:
                pause_at[row.frame] = max(pause_at.get(row.frame, 0), paused)
    paused_frames = sorted(pause_at.items())
    pause_cursor = 0
    paused_in_load = 0

    settled: set[int] = set()
    entry_spawns: dict[int, int] = {}
    load_frame: int | None = None
    area_ids: list[int] = []
    spawn_id: int | None = None
    # Walked from the top of the journal, because most rows do not say where
    # they are — the same reason the endpoint's position walk exists.
    area_now: int | None = None
    co_frame: int = -1                    # the frame the two lists below are for
    co_frame_area_ids: list[int] = []
    anchor_frame: int = -1
    anchor_paused: int = 0

    def close_the_load() -> None:
        nonlocal load_frame, area_ids, spawn_id, paused_in_load
        if load_frame is not None:
            # The last area edge speaks for the load only when no spawn did.
            settled.update(area_ids if spawn_id is not None else area_ids[:-1])
        load_frame, area_ids, spawn_id = None, [], None
        paused_in_load = 0

    def restarts_the_level(frame: int) -> bool:
        if area_now != COURSE_START_AREA:
            return False
        if co_frame != frame or not co_frame_area_ids:
            return True                      # an L-reset in place
        if anchor_frame != frame:
            return False
        return anchor_paused >= PAUSE_WARP_MIN_STREAK

    for row in events:
        payload = row.payload or {}
        # Spend every pause up to and including THIS frame before the tail is
        # judged — a menu warp's anchor lands co-frame with the load's own
        # final area edge, so crediting it later would be crediting it too
        # late to save the row it explains.
        #
        # ONLY UNTIL THE ARRIVAL (`spawn_id is None`), and that bound is the
        # measurement's doing rather than tidiness. A load that kept taking
        # credit after it had already arrived ratcheted itself forward across
        # a whole session of resets, and every spawn after the first stopped
        # being an arrival of its own: SIX vanished across the two real
        # journals, most with no surviving row beside them.
        while (pause_cursor < len(paused_frames)
               and paused_frames[pause_cursor][0] <= row.frame):
            frame_of, paused = paused_frames[pause_cursor]
            if load_frame is not None and frame_of > load_frame and spawn_id is None:
                paused_in_load += paused
            pause_cursor += 1
        if row.type == "area_changed":
            area_now = payload.get("to")
            if co_frame != row.frame:
                co_frame, co_frame_area_ids = row.frame, []
            co_frame_area_ids.append(row.id)
        elif row.type in ("practice_reset", "state_loaded"):
            anchor_frame = row.frame
            anchor_paused = payload.get("paused_frames_before") or 0
        if row.type == "level_changed":
            if payload.get("from") == payload.get("to"):
                continue          # establishing/corrective bookkeeping
            close_the_load()
            load_frame = row.frame
            continue
        # The tail always runs from the LEVEL EDGE. What changes is whether the
        # pause is forgiven: before the arrival it is (a menu warp pauses
        # mid-load), after it the raw rule stands — so the short grace that
        # keeps a death respawn from reading as a second "Started …" is exactly
        # the window it always was, and a pause spent earlier cannot buy the
        # load thousands of frames of afterlife.
        if load_frame is not None:
            credit = paused_in_load if spawn_id is None else 0
            if row.frame - load_frame - credit > LEVEL_LOAD_TAIL_FRAMES:
                close_the_load()
        if load_frame is not None:
            if row.type == "area_changed":
                area_ids.append(row.id)
            elif row.type == "spawned" and spawn_id is None:
                # The FIRST spawn of the load only: a death respawn later in
                # the same course is the player's, and its own load has its
                # own edge.
                spawn_id = row.id
                entry_spawns[row.id] = payload.get("level")
            continue
        if row.type == "spawned" and restarts_the_level(row.frame):
            entry_spawns[row.id] = payload.get("level")
            if co_frame == row.frame:
                settled.update(co_frame_area_ids)
    close_the_load()
    return settled, entry_spawns


# practice_reset/state_loaded/game_reset carry no level/course at all
# (detectors/anchors.py, lifecycle.py) — the matcher resolves WHERE an
# attempt_anchor/reset_game trigger fires from live MatchContext state, not
# from the event's own payload, and label_event only ever sees one row in
# isolation. These three always produce a sentence (never None): "a reset
# happened" is itself the recognisable moment, even with no place attached.
def _practice_reset(payload: dict) -> str:
    igt = payload.get("igt_frames_before")
    if igt is None:
        return "Reset the level"
    return f"Reset the level after {format_igt(igt)}"


def _state_loaded(payload: dict) -> str:
    igt = payload.get("igt_frames_restored")
    if igt is None:
        return "Loaded a savestate"
    return f"Loaded a savestate at {format_igt(igt)}"


def _game_reset(payload: dict) -> str:
    return "Reset the game"


_LABELERS: dict[str, Callable[[dict], str | None]] = {
    "level_changed": _level_changed,
    "area_changed": _area_changed,
    "star_collected": _star_collected,
    "warp_entered": _warp_entered,
    "key_grabbed": _key_grabbed,
    "spawned": _spawned,
    "moment_reached": _moment_reached,
    "practice_reset": _practice_reset,
    "state_loaded": _state_loaded,
    "game_reset": _game_reset,
}

# The journal event types this module can turn into a sentence at all — read
# by test_eventlabel.py's completeness guard against tracking/segments.py's
# TRIGGERS registry. NOT the set of types the timeline should default to
# showing (see the labelling-volume decision above) — a type being in here
# says nothing about how a later task chooses to display it.
#
# THE GUARD'S BLIND SPOT: test_every_trigger_type_has_a_labellable_event_shape
# fails the day a NEW TriggerType lands with no entry in its trigger->journal-
# type mapping, but it reads that mapping as a hand-written literal, not from
# TRIGGERS' own match lambdas — so if an EXISTING trigger's match lambda were
# edited to dispatch on a different journal type, the guard's literal mapping
# would go stale silently and keep passing. Living with this rather than
# building lambda introspection to close it.
LABELLABLE_TYPES = frozenset(_LABELERS)

# Maps each TriggerType KEY (segments.py's matcher vocabulary: level_enter/
# level_exit/area_enter/...) to the journal event TYPE(S) that can actually
# fire it — read off each TriggerType's own match lambda in TRIGGERS by hand
# (see THE GUARD'S BLIND SPOT above: this is that hand-written literal).
# Trigger KEYS and journal event TYPES are two different vocabularies (the
# matcher vocabulary vs. the wire format); label_event never dispatches on a
# string like "level_enter" or "reset_game", only on real event types like
# "level_changed" or "game_reset". ONE DOOR — test_eventlabel.py's
# completeness guard and test_api.py's default-view sole-route test both
# read this same constant rather than each carrying their own copy (a second
# hand-written copy is exactly how the task-11 revision's wrong `attempt_
# anchor` count first got in: it was hand-copied from a docstring instead of
# re-derived).
TRIGGER_JOURNAL_TYPES: dict[str, frozenset[str]] = {
    "level_enter": frozenset({"level_changed"}),
    "level_exit": frozenset({"level_changed"}),
    "area_enter": frozenset({"area_changed"}),
    "warp_entered": frozenset({"warp_entered"}),
    # Two conditions, ONE journal event: `warp_entered` names where you are,
    # `entrance_touched` names where the entrance leads (task 0081).
    "entrance_touched": frozenset({"warp_entered"}),
    "key_grabbed": frozenset({"key_grabbed"}),
    "star_grabbed": frozenset({"star_collected"}),
    "spawned": frozenset({"spawned"}),
    "moment_reached": frozenset({"moment_reached"}),
    "attempt_anchor": frozenset({"practice_reset", "state_loaded"}),
    "reset_game": frozenset({"game_reset"}),
}


def label_event(row, names: dict | None = None) -> str | None:
    """`names` is the LANDMARK CATALOGUE (key -> name); only the
    labellers listed in `_READS_THE_CATALOGUE` read it, so adding an
    ordinary labeller never has to know the catalogue exists.

    row: an EventRow (storage/db.py) — .type and .payload are read; .id,
    .frame, .wall_time_utc are not needed for the sentence itself. Returns
    None for a non-boundary type, or for a boundary-capable type whose
    payload lacks the field(s) needed to name what happened (an
    establishing/corrective bookkeeping event, or a legacy journal row from
    before a field existed)."""
    labeler = _LABELERS.get(row.type)
    if labeler is None:
        return None
    if row.type in _READS_THE_CATALOGUE:
        return labeler(row.payload, names or {})
    return labeler(row.payload)
