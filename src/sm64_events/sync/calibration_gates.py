"""Gates that MEASURE a live number beside a shipped constant, rather than
verifying an address. By the time any of these runs, `address_gates.py` has
already finished — the working layout is complete, so `ctx.snapshots()` can
build a `SnapshotReader` over it, and `sync/stack.py::DetectorRun` can run
the REAL shipped detector chain (`main.build_detectors`) over that stream.
Every check below does exactly that: collect a trace, run it through the
real detectors (so an unwired detector shows up here, not just in
`tests/test_gates_cover.py`), then compare a MEASURED number against the
constant its `backs` names.

A `Verdict("failed", measured={...}, evidence=...)` here is not a bug in
this file — it is the FINDING the whole version-sync effort exists to
surface: "the JP number disagrees with the US constant." No calibration
constant is ever version-keyed until a measurement like this disagrees, so
nothing here may EVER restate a constant's value as a literal; every
comparison reads it live via `sync.checks.resolve_backs` (or a plain
`getattr` chain for a class attribute already imported by name), which is
also what makes `tests/test_gates_cover.py`'s "every `backs` resolves by
import" enforcement meaningful.

`cal.warp.pipe_dest_delay`'s comparison is a documented DEVIATION from Task
10's literal "verified iff == 20": 20 is a floor mentioned only in a
`WarpDetector` comment (`probe_warp_block`'s own finding), never an
importable name, and restating it here as a bare literal is exactly what
this module's own rule (previous paragraph) forbids. `RIDE_WINDOW_FRAMES`
(26) is the actual constant `warp.py` depends on — the CEILING a real touch
must land inside so the detector's own pause-detection logic stays correct
— so the check verifies the measured delay stays under THAT window instead.
Flagged in the Track D report for a human call on which reading the
dashboard should show.
"""
from sm64_events.memory import addresses as A
from sm64_events.sync.checks import (await_event, box_open_index, first_edge,
                                     resolve_backs)
from sm64_events.sync.gates import Gate, Verdict, register
from sm64_events.sync.stack import DetectorRun

STAR_GRAB_TRACE_SECONDS = 20.0
# Task 9's own number: how long after `star_collected` publishes the result
# store should have settled (verify_star_stop.py's finding).
RESULT_SETTLE_WAIT_FRAMES = 45
TEXTBOX_TRACE_SECONDS = 30.0
WARP_TRACE_SECONDS = 15.0

SNAPSHOT_REQUIRED_GATES = (
    # Every field core/snapshot.py::_REQUIRED_FIELDS dereferences — the exact
    # set that must be VERIFIED before `ctx.snapshots()` can build a reader
    # (SnapshotReader.require() raises LayoutIncomplete otherwise). Named
    # here once so every gate below states the same dependency the same way.
    "address.global_timer", "address.mario_struct", "address.curr_level",
    "address.curr_area", "address.last_completed_course",
    "address.last_completed_star", "address.pending_warp_op",
    "address.delayed_warp_timer", "address.warp_dest", "address.object_pool",
    "address.usamune_overall", "address.usamune_star_result",
)


def _is_ground_grab(payload: dict) -> bool:
    """star_grab.py enters the dance on the SAME frame as the grab for a
    ground star (`frame == grab_frame`); a midair grab passes through
    ACT_FALL_AFTER_STAR_GRAB first, so its published frame is later."""
    return payload.get("frame") == payload.get("grab_frame")


def _check_star_igt(ctx, *, want_ground: bool) -> Verdict:
    run = DetectorRun(ctx.version)
    result_addr = ctx.candidate("usamune_star_result")
    star_event = None
    for _snap, events in run.stream(ctx.snapshots(STAR_GRAB_TRACE_SECONDS)):
        found = await_event(events, "star_collected")
        if found is not None:
            star_event = found
            break
    if star_event is None:
        return Verdict("failed", evidence=f"no star_collected in "
                                          f"{STAR_GRAB_TRACE_SECONDS:.0f}s")
    payload = star_event["payload"]
    payload = payload | {"frame": star_event.get("frame")}
    is_ground = _is_ground_grab(payload)
    if is_ground != want_ground:
        kind = "ground" if is_ground else "midair"
        return Verdict("failed", evidence=f"that was a {kind} grab, not the "
                                          f"{'ground' if want_ground else 'midair'} "
                                          "kind this gate asked for")
    ctx.sleep(RESULT_SETTLE_WAIT_FRAMES / 30.0)
    settled = ctx.raw().read_u16(result_addr)
    measured = {"published_minus_result": payload["igt_frames"] - settled,
               "published_after": payload["published_after"]}
    if payload["igt_frames"] == settled:
        return Verdict("verified", measured=measured,
                       evidence=f"published igt {payload['igt_frames']} equals "
                                "the settled result")
    return Verdict("failed", measured=measured,
                   evidence=f"published igt {payload['igt_frames']} != settled "
                            f"result {settled}")


def _check_star_result_write_delay(ctx) -> Verdict:
    """The write into `usamune_star_result` and the detector's PUBLISH of
    `star_collected` are two different moments — `IgtClock` deliberately
    will not trust a single sighting of a changed result and waits out the
    full settle window before it publishes (measured while writing this
    gate's own test: a write seen 4 frames after the grab still published
    50 frames later). So `write_frame` is latched the instant the raw field
    changes, independent of whether the event has published yet — gating it
    on `grab_frame` already being known would measure "how long the
    DETECTOR took to believe it", not the fact this gate exists to measure."""
    run = DetectorRun(ctx.version)
    grab_frame = None
    baseline_result = None
    write_frame = None
    for snap, events in run.stream(ctx.snapshots(STAR_GRAB_TRACE_SECONDS)):
        if baseline_result is None:
            baseline_result = snap.igt_result
        found = await_event(events, "star_collected")
        if found is not None and grab_frame is None:
            grab_frame = found["payload"]["grab_frame"]
        if write_frame is None and snap.igt_result != baseline_result:
            write_frame = snap.global_timer
        if grab_frame is not None and write_frame is not None:
            break
    if grab_frame is None:
        return Verdict("failed", evidence=f"no star_collected in "
                                          f"{STAR_GRAB_TRACE_SECONDS:.0f}s")
    if write_frame is None:
        return Verdict("failed", evidence="usamune_star_result never changed "
                                          "after the grab")
    delay = write_frame - grab_frame
    measured = {"delay_frames": delay}
    threshold = resolve_backs(
        "sm64_events.detectors.igt_clock.IgtClock.RESULT_FRESH_FRAMES")
    if delay <= threshold:
        return Verdict("verified", measured=measured,
                       evidence=f"result store changed {delay} frame(s) after "
                                f"the grab (<= {threshold})")
    return Verdict("failed", measured=measured,
                   evidence=f"result store changed {delay} frame(s) after the "
                            f"grab (> {threshold})")


register(Gate(
    id="cal.star.ground_igt", feature="star grab", kind="calibration",
    needs=SNAPSHOT_REQUIRED_GATES,
    backs="sm64_events.detectors.igt_clock.IgtClock.DISPLAY_TICK",
    timeout_s=STAR_GRAB_TRACE_SECONDS + 10,
    instruction="Grab a star while standing on the ground.",
    proves="a ground grab's published igt_frames equals Usamune's own "
           "settled result.",
    check=lambda ctx: _check_star_igt(ctx, want_ground=True),
))
register(Gate(
    id="cal.star.midair_igt", feature="star grab", kind="calibration",
    needs=SNAPSHOT_REQUIRED_GATES,
    backs="sm64_events.detectors.igt_clock.IgtClock.DISPLAY_TICK",
    timeout_s=STAR_GRAB_TRACE_SECONDS + 10,
    instruction="Grab a star while in the air (jump into it).",
    proves="a midair grab's published igt_frames equals Usamune's own "
           "settled result.",
    check=lambda ctx: _check_star_igt(ctx, want_ground=False),
))
register(Gate(
    id="cal.star.result_write_delay", feature="star grab", kind="calibration",
    needs=SNAPSHOT_REQUIRED_GATES,
    backs="sm64_events.detectors.igt_clock.IgtClock.RESULT_FRESH_FRAMES",
    timeout_s=STAR_GRAB_TRACE_SECONDS + 10,
    instruction="Grab any star.",
    proves="how many frames after the grab Usamune's result store actually "
           "changes, against the constant IgtClock trusts it within.",
    check=_check_star_result_write_delay,
))


def _check_textbox_open_state(ctx) -> Verdict:
    snaps = list(ctx.snapshots(TEXTBOX_TRACE_SECONDS))
    run = DetectorRun(ctx.version)
    run.run(snaps)   # exercises the real chain; the measurement below reads
                     # the snapshots directly, same as probe_textbox.py did
    reading_index = first_edge(snaps, A.DIALOG_ACTIONS)
    if reading_index is None:
        return Verdict("failed", evidence=f"no dialog action edge in "
                                          f"{TEXTBOX_TRACE_SECONDS:.0f}s")
    thresholds = resolve_backs("sm64_events.memory.addresses.BOX_OPENS_AT_STATE")
    opened_index = box_open_index(snaps, reading_index, thresholds)
    if opened_index is None:
        return Verdict("failed", evidence="action_state never reached the "
                                          "box-open threshold")
    action = snaps[reading_index].mario_action
    measured_state = snaps[opened_index].mario_action_state
    expected = thresholds[action]
    measured = {"action_state": measured_state, "expected": expected}
    if measured_state == expected:
        return Verdict("verified", measured=measured,
                       evidence=f"box opens at state {measured_state}, "
                                "matching the constant")
    return Verdict("failed", measured=measured,
                   evidence=f"box opens at state {measured_state}, constant "
                            f"says {expected}")


register(Gate(
    id="cal.textbox.open_state", feature="textboxes", kind="calibration",
    needs=SNAPSHOT_REQUIRED_GATES,
    backs="sm64_events.memory.addresses.BOX_OPENS_AT_STATE",
    timeout_s=TEXTBOX_TRACE_SECONDS + 10,
    instruction="Talk to any NPC (Toad in the lobby).",
    proves="the action_state value on the frame the box actually appears "
           "matches BOX_OPENS_AT_STATE for that action.",
    check=_check_textbox_open_state,
))


def _dest_tuple(snap):
    return (snap.warp_dest_type, snap.warp_dest_level, snap.warp_dest_area,
            snap.warp_dest_node)


def _warp_touch_measurement(snaps) -> dict | None:
    """The touch edge, and how many frames later sWarpDest changed — the
    same fact `probe_warp_block.py::report` prints per touch, over the
    already-collected snapshot list instead of a live poll."""
    edge_index = first_edge(snaps, A.WARP_ENTRY_ACTIONS)
    if edge_index is None:
        return None
    dest_before = _dest_tuple(snaps[edge_index])
    dest_changed_index = None
    for index in range(edge_index, len(snaps)):
        if _dest_tuple(snaps[index]) != dest_before:
            dest_changed_index = index
            break
    delay = (None if dest_changed_index is None else
             snaps[dest_changed_index].global_timer - snaps[edge_index].global_timer)
    return {"delay_frames": delay}


def _check_warp_delay(ctx, *, threshold_name: str, compare) -> Verdict:
    snaps = list(ctx.snapshots(WARP_TRACE_SECONDS))
    run = DetectorRun(ctx.version)
    run.run(snaps)
    trace = _warp_touch_measurement(snaps)
    if trace is None:
        return Verdict("failed", evidence=f"no warp-entry action edge in "
                                          f"{WARP_TRACE_SECONDS:.0f}s")
    delay = trace["delay_frames"]
    if delay is None:
        return Verdict("failed", evidence="sWarpDest never changed after the touch")
    threshold = resolve_backs(threshold_name)
    measured = {"delay_frames": delay, "window": threshold}
    if compare(delay, threshold):
        return Verdict("verified", measured=measured,
                       evidence=f"sWarpDest changed {delay} frame(s) after "
                                f"the touch (window {threshold})")
    return Verdict("failed", measured=measured,
                   evidence=f"sWarpDest changed {delay} frame(s) after the "
                            f"touch (window {threshold})")


register(Gate(
    id="cal.warp.pipe_dest_delay", feature="warps & entrances", kind="calibration",
    needs=SNAPSHOT_REQUIRED_GATES,
    backs="sm64_events.detectors.warp.WarpDetector.RIDE_WINDOW_FRAMES",
    timeout_s=WARP_TRACE_SECONDS + 10,
    instruction="Jump into the BitDW pipe.",
    proves="the real touch-to-destination delay stays inside "
           "RIDE_WINDOW_FRAMES, the ceiling warp.py's own pause detection "
           "depends on.",
    check=lambda ctx: _check_warp_delay(
        ctx, threshold_name="sm64_events.detectors.warp.WarpDetector.RIDE_WINDOW_FRAMES",
        compare=lambda delay, window: delay < window),
))
register(Gate(
    id="cal.warp.painting_at_touch", feature="warps & entrances", kind="calibration",
    needs=SNAPSHOT_REQUIRED_GATES,
    backs="sm64_events.detectors.warp.WarpDetector.FRESH_WINDOW_FRAMES",
    timeout_s=WARP_TRACE_SECONDS + 10,
    instruction="Jump into the WF painting.",
    proves="a painting's sWarpDest write lands inside FRESH_WINDOW_FRAMES of "
           "the touch — the rule that lets warp.py publish a painting with "
           "no wait.",
    check=lambda ctx: _check_warp_delay(
        ctx, threshold_name="sm64_events.detectors.warp.WarpDetector.FRESH_WINDOW_FRAMES",
        compare=lambda delay, window: delay <= window),
))

register(Gate(
    id="cal.moment.display_lag", feature="landmarks", kind="calibration", auto=True,
    backs="sm64_events.detectors.moment.MomentDetector.DISPLAY_LAG_FRAMES",
    instruction="Run tools/score_moment_clock.py with a screenshot of "
               "Usamune's timer — this gate cannot be answered live.",
    proves="whether DISPLAY_LAG_FRAMES matches the screenshot-scored offset "
           "(deferred to the standalone tool; see its own module docstring).",
    check=lambda ctx: Verdict("skipped", evidence="needs a screenshot; run "
                                                   "tools/score_moment_clock.py"),
))
