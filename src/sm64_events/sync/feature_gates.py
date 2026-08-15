"""feature_gates -- one gate per event KIND a shipped detector emits.

A feature gate answers the parity question directly: "does the real detector
stack, reading THIS version's layout, publish what it publishes on US?" It
runs `sync.stack.DetectorRun` -- the exact chain `main.build_detectors`
wires, in the exact order `server/poller.py` feeds it -- over a live
snapshot stream, and watches for the wire event the instruction asked the
human to cause. `tests/test_sync_feature_gates.py` requires a
`feature.<type>*` gate for every `type="..."` string in `detectors/*.py`, so
a shipped detector with no gate is a red build (the "stay in sync" mechanism
the registry exists for -- see sync/gates.py's module docstring).

WHY EVERY GATE NEEDS THE FULL ADDRESS SET. `core/snapshot.py::SnapshotReader`
refuses to build over an incomplete layout -- it requires all twelve fields
`_LAYOUT_NEEDS` names before it will read anything, whether or not the
detector under test happens to touch a given one. So every feature gate
needs every one of those twelve `address.*` gates verified first, and a check
whose predicate names a behaviour SYMBOL (a door, a sign, a pole, a bob-omb)
needs `behaviour.base` on top of that: without it
`memory/behaviours.py::symbol_of` degrades every pointer to `ptr_xxxxxxxx`
and no symbol match can ever pass.

`_await` and `_await_sequence` are the one door every check runs through:
they drive `DetectorRun.stream` tick by tick and stop the INSTANT the wanted
event lands, so a live run never waits out its full timeout once the human
has done what was asked. `_check_key` is the one exception -- a
`key_grabbed` verdict also asserts a NEGATIVE (no `star_collected` leaked in
the following window, the JP star-model-at-the-key leak this gate exists to
catch), so it deliberately keeps watching past the match.

Where a payload has to match what the human just did (which star, which
course), the check asks first via `ctx.prompt` -- `_ask_course_star` is the
one place that parses the answer, so every star gate reads it the same way.
"""
from collections.abc import Callable, Iterable

from sm64_events.memory import addresses as A
from sm64_events.sync import checks
from sm64_events.sync.gates import Gate, Verdict, register
from sm64_events.sync.stack import DetectorRun

# Every feature gate reads through SnapshotReader, which will not even build
# unless every one of these twelve fields is present
# (core/snapshot.py::_REQUIRED_FIELDS) -- so every feature gate needs every
# one of them, regardless of which fields the detector under test actually
# touches.
_LAYOUT_NEEDS = (
    "address.global_timer", "address.mario_struct", "address.curr_level",
    "address.curr_area", "address.last_completed_course",
    "address.last_completed_star", "address.pending_warp_op",
    "address.delayed_warp_timer", "address.warp_dest", "address.object_pool",
    "address.usamune_overall", "address.usamune_star_result",
)
# Plus the behaviour base, for a check whose predicate names a SYMBOL rather
# than an id or a level -- without it every pointer reads as ptr_xxxxxxxx.
_SYMBOL_NEEDS = _LAYOUT_NEEDS + ("behaviour.base",)


def _level_id(name: str) -> int:
    """A LEVEL_NAMES value back to its id -- reads better at a call site than
    a bare literal, and needs no new constant added to addresses.py."""
    return next(level for level, level_name in A.LEVEL_NAMES.items()
               if level_name == name)


def _ask_course_star(ctx, what: str) -> tuple[int, int]:
    text = ctx.prompt(f"{what} -- course id,star id (e.g. 24,1): ")
    course, star = text.split(",", 1)
    return int(course.strip()), int(star.strip())


def _seen_types(run: DetectorRun) -> list[str]:
    return sorted({event["type"] for event in run.events})


def _await(ctx, type_: str,
          predicate: Callable[[dict], bool] = lambda payload: True, *,
          seconds: float | None = None
          ) -> tuple[Verdict, dict | None, DetectorRun]:
    """Run the real stack until `type_` fires and `predicate(payload)` holds,
    or the clock runs out. Stops on the first match."""
    run = DetectorRun(ctx.version)
    duration = ctx.timeout_s if seconds is None else seconds
    for _snapshot, _new_events in run.stream(ctx.snapshots(duration)):
        found = checks.await_event(run.events, type_, predicate)
        if found is not None:
            return (Verdict("verified", evidence=f"{type_}: {found['payload']}",
                            frames=found["frame"]),
                    found, run)
    return (Verdict("failed", evidence=(
                f"timed out after {duration:.0f}s waiting for {type_}; "
                f"saw: {_seen_types(run)}")),
            None, run)


def _await_sequence(ctx, steps: Iterable[tuple[str, Callable[[dict], bool]]],
                    *, seconds: float | None = None
                    ) -> tuple[Verdict, list[dict], DetectorRun]:
    """Like `_await`, but for an ORDERED chain of events over ONE stream --
    the Nth step's match must come from events seen after the (N-1)th's. The
    inner `while` lets two steps land on the SAME tick (a held event and its
    correction can release together) without one swallowing the other."""
    steps = list(steps)
    run = DetectorRun(ctx.version)
    duration = ctx.timeout_s if seconds is None else seconds
    matched: list[dict] = []
    already_scanned = 0
    for _snapshot, _new_events in run.stream(ctx.snapshots(duration)):
        while len(matched) < len(steps):
            type_, predicate = steps[len(matched)]
            remaining = run.events[already_scanned:]
            found = checks.await_event(remaining, type_, predicate)
            if found is None:
                break
            matched.append(found)
            already_scanned += remaining.index(found) + 1
        if len(matched) == len(steps):
            break
    if len(matched) == len(steps):
        evidence = "; ".join(f"{type_}: {event['payload']}"
                             for (type_, _predicate), event in zip(steps, matched))
        return (Verdict("verified", evidence=evidence, frames=matched[-1]["frame"]),
                matched, run)
    return (Verdict("failed", evidence=(
                f"stalled at step {len(matched) + 1}/{len(steps)} "
                f"({steps[len(matched)][0]}) after {duration:.0f}s; "
                f"saw: {_seen_types(run)}")),
            matched, run)


def _check_key(ctx, which: str, window_s: float = 10.0) -> Verdict:
    """`key_grabbed which=which`, then a NEGATIVE watched for `window_s` more
    seconds: JP shows a star model at the key, so this is the check that
    catches a leaked `star_collected` the fight-end grab must never produce."""
    run = DetectorRun(ctx.version)
    window_frames = round(window_s * 30)
    key_event: dict | None = None
    for snapshot, _new_events in run.stream(ctx.snapshots(ctx.timeout_s)):
        if key_event is None:
            key_event = checks.await_event(
                run.events, "key_grabbed",
                lambda payload, want=which: payload.get("which") == want)
        elif snapshot.global_timer - key_event["frame"] >= window_frames:
            break
    if key_event is None:
        return Verdict("failed", evidence=(
            f"no key_grabbed which={which} within {ctx.timeout_s:.0f}s; "
            f"saw: {_seen_types(run)}"))
    leak = next((event for event in run.events
                if event["type"] == "star_collected"
                and key_event["frame"] <= event["frame"]
                <= key_event["frame"] + window_frames), None)
    if leak is not None:
        return Verdict("failed", evidence=(
            f"star_collected leaked {leak['frame'] - key_event['frame']} "
            f"frames after the {which} key: {leak['payload']}"))
    return Verdict("verified", frames=key_event["frame"], evidence=(
        f"key_grabbed which={which}, no star_collected within "
        f"{window_s:.0f}s"))


# --- star grab ---------------------------------------------------------

def _check_star_ground(ctx) -> Verdict:
    course, star = _ask_course_star(ctx, "Grab a star while standing on the ground")
    verdict, _found, _run = _await(ctx, "star_collected", lambda payload: (
        payload.get("course_id") == course and payload.get("star_id") == star
        and payload.get("igt_source") in ("result", "counter")))
    return verdict


def _check_star_midair(ctx) -> Verdict:
    course, star = _ask_course_star(ctx, "Grab a star while in the air (jump into it)")
    verdict, _found, _run = _await(ctx, "star_collected", lambda payload: (
        payload.get("course_id") == course and payload.get("star_id") == star
        and payload.get("igt_source") in ("result", "counter")))
    return verdict


def _check_star_time_corrected(ctx) -> Verdict:
    course, star = _ask_course_star(
        ctx, "Grab a subarea star (SSL: Inside the Ancient Pyramid works) so "
             "the whole-star correction follows")
    verdict, _matched, _run = _await_sequence(ctx, [
        ("star_collected", lambda payload: (
            payload.get("course_id") == course and payload.get("star_id") == star)),
        ("star_time_corrected", lambda payload: (
            payload.get("course_id") == course and payload.get("star_id") == star)),
    ])
    return verdict


# --- warps & entrances ---------------------------------------------------

def _check_warp(ctx, level_name: str) -> Verdict:
    target = _level_id(level_name)
    verdict, _found, _run = _await(
        ctx, "warp_entered", lambda payload: payload.get("to") == target)
    return verdict


# --- castle areas / anything that just needs to fire once ------------------

def _check_event(ctx, type_: str) -> Verdict:
    verdict, _found, _run = _await(ctx, type_)
    return verdict


# --- landmarks / textboxes -------------------------------------------------

def _check_moment(ctx, kind: str, symbols: tuple[str, ...]) -> Verdict:
    def predicate(payload: dict) -> bool:
        if payload.get("kind") != kind:
            return False
        landmark = payload.get("landmark")
        return landmark is not None and landmark.get("symbol") in symbols
    verdict, _found, _run = _await(ctx, "moment_reached", predicate)
    return verdict


# --- caused moments ---------------------------------------------------------

def _check_caused(ctx, kind: str) -> Verdict:
    verdict, _found, _run = _await(
        ctx, "moment_reached", lambda payload: payload.get("kind") == kind)
    return verdict


# --- keys & Bowser -----------------------------------------------------

def _check_key_b1(ctx) -> Verdict:
    return _check_key(ctx, "bitdw")


def _check_key_grand(ctx) -> Verdict:
    answer = ctx.prompt(
        "Beat Bowser 3 for this gate? (y/n, default n): ").strip().lower()
    if not answer.startswith("y"):
        return Verdict("skipped", evidence=(
            "declined -- rerun with --only feature.key_grabbed.grand"))
    return _check_key(ctx, "grand")


# Kept as its own named tuple (rather than relying on sync.gates.GATES, which
# other test files clear and repopulate) so tests/test_sync_feature_gates.py
# can address these gates without depending on what else has (or has not)
# registered itself in the shared registry this session.
FEATURE_GATES: tuple[Gate, ...] = (
    Gate("feature.star_collected.ground", "star grab", "feature",
        "Grab a star while standing on the ground.",
        "star_collected fires with the grabbed course/star and a real IGT "
        "source.",
        _check_star_ground, needs=_LAYOUT_NEEDS),
    Gate("feature.star_collected.midair", "star grab", "feature",
        "Grab a star while in the air (jump into it).",
        "star_collected fires with the grabbed course/star and a real IGT "
        "source.",
        _check_star_midair, needs=_LAYOUT_NEEDS),
    Gate("feature.star_time_corrected", "star grab", "feature",
        "Grab a subarea star (SSL: Inside the Ancient Pyramid works).",
        "the whole-star correction follows its star_collected for the same "
        "star.",
        _check_star_time_corrected, needs=_LAYOUT_NEEDS),

    Gate("feature.warp_entered.pipe", "warps & entrances", "feature",
        "Jump into the BitDW pipe (basement).",
        "warp_entered fires naming Bowser in the Dark World as the "
        "destination.",
        lambda ctx: _check_warp(ctx, "Bowser in the Dark World"),
        needs=_LAYOUT_NEEDS),
    Gate("feature.warp_entered.painting", "warps & entrances", "feature",
        "Jump into the WF painting.",
        "warp_entered fires naming Whomp's Fortress as the destination.",
        lambda ctx: _check_warp(ctx, "Whomp's Fortress"), needs=_LAYOUT_NEEDS),
    Gate("feature.warp_entered.bbh", "warps & entrances", "feature",
        "Jump into the BBH cage.",
        "warp_entered fires naming Big Boo's Haunt as the destination.",
        lambda ctx: _check_warp(ctx, "Big Boo's Haunt"), needs=_LAYOUT_NEEDS),

    Gate("feature.level_changed", "castle areas", "feature",
        "Walk from the castle lobby into any course.",
        "level_changed fires on the course entry.",
        lambda ctx: _check_event(ctx, "level_changed"), needs=_LAYOUT_NEEDS),
    Gate("feature.stage_changed", "castle areas", "feature",
        "Walk from the castle lobby into any course.",
        "stage_changed fires on the course entry.",
        lambda ctx: _check_event(ctx, "stage_changed"), needs=_LAYOUT_NEEDS),
    Gate("feature.area_changed", "castle areas", "feature",
        "Walk from the lobby down to the basement.",
        "area_changed fires on the castle area edge.",
        lambda ctx: _check_event(ctx, "area_changed"), needs=_LAYOUT_NEEDS),

    Gate("feature.moment_reached.door_open", "landmarks", "feature",
        "Open the WF door.",
        "moment_reached fires kind=door_open with a bhvDoor landmark.",
        lambda ctx: _check_moment(ctx, "door_open", ("bhvDoor",)),
        needs=_SYMBOL_NEEDS),
    Gate("feature.moment_reached.textbox", "textboxes", "feature",
        "Read the lobby sign.",
        "moment_reached fires kind=textbox with a message-sign landmark.",
        lambda ctx: _check_moment(ctx, "textbox",
                                  ("bhvMessagePanel", "bhvSignOnWall")),
        needs=_SYMBOL_NEEDS),
    Gate("feature.moment_reached.pole_grab", "landmarks", "feature",
        "Grab the WF pole.",
        "moment_reached fires kind=pole_grab with a bhvPoleGrabbing "
        "landmark.",
        lambda ctx: _check_moment(ctx, "pole_grab", ("bhvPoleGrabbing",)),
        needs=_SYMBOL_NEEDS),
    Gate("feature.moment_reached.pickup", "landmarks", "feature",
        "Pick up a bob-omb.",
        "moment_reached fires kind=pickup with a bhvBobomb landmark.",
        lambda ctx: _check_moment(ctx, "pickup", ("bhvBobomb",)),
        needs=_SYMBOL_NEEDS),

    Gate("feature.moment_reached.switch_press", "caused moments", "feature",
        "Press the blue-coin switch in WF.",
        "moment_reached fires kind=switch_press.",
        lambda ctx: _check_caused(ctx, "switch_press"), needs=_SYMBOL_NEEDS),
    Gate("feature.moment_reached.enemy_defeated", "caused moments", "feature",
        "Stomp a goomba in BoB.",
        "moment_reached fires kind=enemy_defeated.",
        lambda ctx: _check_caused(ctx, "enemy_defeated"), needs=_SYMBOL_NEEDS),

    Gate("feature.key_grabbed.b1", "keys & Bowser", "feature",
        "Beat Bowser 1 and grab the key.",
        "key_grabbed which=bitdw fires with no star_collected leaking in "
        "behind it (JP shows a star model at the key).",
        _check_key_b1, needs=_LAYOUT_NEEDS),
    Gate("feature.key_grabbed.grand", "keys & Bowser", "feature",
        "Beat Bowser 3 and grab the grand star (optional -- you can "
        "decline).",
        "key_grabbed which=grand fires with no star_collected leaking in "
        "behind it, when you choose to run it.",
        _check_key_grand, needs=_LAYOUT_NEEDS),

    Gate("feature.death", "death", "feature",
        "Die in any course.",
        "death fires with a recognised cause.",
        lambda ctx: _check_event(ctx, "death"), needs=_LAYOUT_NEEDS),

    Gate("feature.game_reset", "spawn & reset", "feature",
        "Reset the game (power-cycle / ROM reload through Usamune).",
        "game_reset fires on the console reset.",
        lambda ctx: _check_event(ctx, "game_reset"), needs=_LAYOUT_NEEDS),
    Gate("feature.spawned", "spawn & reset", "feature",
        "File-select spawn into a fresh save.",
        "spawned fires on the file-select spawn.",
        lambda ctx: _check_event(ctx, "spawned"), needs=_LAYOUT_NEEDS),
    Gate("feature.practice_reset", "spawn & reset", "feature",
        "Trigger a Usamune level reset mid-attempt.",
        "practice_reset fires on the mid-attempt reset.",
        lambda ctx: _check_event(ctx, "practice_reset"), needs=_LAYOUT_NEEDS),
    Gate("feature.mario_acted", "spawn & reset", "feature",
        "Move Mario at all after any reset or spawn.",
        "mario_acted fires on the first post-reset action.",
        lambda ctx: _check_event(ctx, "mario_acted"), needs=_LAYOUT_NEEDS),
    Gate("feature.state_loaded", "spawn & reset", "feature",
        "Load a savestate (Usamune).",
        "state_loaded fires on the savestate load.",
        lambda ctx: _check_event(ctx, "state_loaded"), needs=_LAYOUT_NEEDS),
)

register(*FEATURE_GATES)
