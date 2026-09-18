"""The overlay child: what it does with a control line, and how it ends.

The real child process is exercised here with `--backend none`, a host that
records what a window would have been told instead of making one, and with
`--fixed-rect`, which stands in for Project64 so the suite never queries the
emulator's window. Everything else -- argument parsing, the control loop, the
follow loop, the exit paths -- is the code that ships.
"""
import io
import json
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.window.child import (
    ControlPlane,
    FollowLoop,
    OverlayState,
    build_parser,
    run_control_loop,
)
from sm64_events.ghost.window.geometry import GameWindow, Placement, Rect
from sm64_events.ghost.window.host import NullHost

GHOST_URL = "http://127.0.0.1:8065/ui/ghost/index.html?kind=star&entity_key=bob-1"
OTHER_URL = "http://127.0.0.1:8065/ui/ghost/index.html?kind=segment&entity_key=s1"
PICTURE = Rect(x=10, y=20, width=640, height=480)


def make_plane(url=GHOST_URL, opacity=0.5, visible=False):
    host = NullHost()
    events = []
    plane = ControlPlane(host=host,
                         state=OverlayState(url=url, opacity=opacity, visible=visible),
                         emit=events.append)
    return plane, host, events


def test_a_control_line_reaches_the_window_and_the_wanted_state():
    plane, host, _ = make_plane()
    keep_going = plane.apply_line(json.dumps(
        {"url": OTHER_URL, "opacity": 0.25, "visible": True}))
    assert keep_going is True
    assert plane.state.url == OTHER_URL
    assert plane.state.opacity == pytest.approx(0.25)
    assert plane.state.visible is True
    assert ("load_url", OTHER_URL) in host.calls
    assert ("set_alpha", 64) in host.calls


def test_visibility_is_wanted_state_not_a_second_way_to_show_a_window():
    """One place shows and hides the overlay: the follow loop's decision. The
    control plane only records what the player asked for."""
    plane, host, _ = make_plane()
    plane.apply_line('{"visible": true}')
    assert plane.state.visible is True
    assert not [call for call in host.calls if call[0] in ("show", "hide")]


def test_quit_stops_the_loop():
    plane, _, _ = make_plane()
    assert plane.apply_line('{"quit": true}') is False


def test_a_line_we_cannot_read_is_reported_and_survived():
    plane, host, events = make_plane()
    assert plane.apply_line("}{ not json") is True
    assert any(event.get("event") == "rejected" for event in events)
    assert host.calls == []


def test_the_control_loop_runs_to_the_quit_line():
    plane, host, _ = make_plane()
    stream = io.StringIO('{"opacity": 1.0}\n\n{"quit": true}\n{"opacity": 0.0}\n')
    assert run_control_loop(stream, plane) == "quit"
    assert ("set_alpha", 255) in host.calls
    assert ("set_alpha", 0) not in host.calls, "nothing after quit is read"


def test_the_child_ends_when_its_parent_goes_away():
    """The trainer holds the write end of this pipe. If the trainer dies the
    pipe closes, the read returns nothing, and the overlay must go with it --
    that is what keeps a crash from leaving a window on the player's screen."""
    plane, _, _ = make_plane()
    assert run_control_loop(io.StringIO(""), plane) == "parent_gone"


def test_the_follow_loop_places_the_overlay_once_per_change():
    seen = []
    game = [GameWindow(found=True, minimized=False, client=PICTURE)]
    loop = FollowLoop(observe=lambda: game[0], apply=seen.append,
                      wanted=lambda: True)

    first = loop.tick()
    assert first == Placement(show=True, rect=PICTURE, reason="follow")
    assert loop.tick() is None, "a window that has not moved is not re-placed"
    assert len(seen) == 1

    moved = Rect(x=300, y=20, width=640, height=480)
    game[0] = GameWindow(found=True, minimized=False, client=moved)
    assert loop.tick().rect == moved
    assert len(seen) == 2


def test_the_follow_loop_hides_when_project64_is_minimized_and_returns_after():
    game = [GameWindow(found=True, minimized=False, client=PICTURE)]
    loop = FollowLoop(observe=lambda: game[0], apply=lambda _p: None,
                      wanted=lambda: True)
    loop.tick()
    game[0] = GameWindow(found=True, minimized=True, client=PICTURE)
    assert loop.tick().show is False
    game[0] = GameWindow(found=True, minimized=False, client=PICTURE)
    assert loop.tick().show is True


def test_the_follow_loop_obeys_the_wanted_switch():
    wanted = [False]
    loop = FollowLoop(
        observe=lambda: GameWindow(found=True, minimized=False, client=PICTURE),
        apply=lambda _p: None, wanted=lambda: wanted[0])
    assert loop.tick().reason == "hidden_by_request"
    wanted[0] = True
    assert loop.tick().show is True


def test_the_follow_loop_scales_what_it_places():
    loop = FollowLoop(
        observe=lambda: GameWindow(found=True, minimized=False, client=PICTURE),
        apply=lambda _p: None, wanted=lambda: True, scale=lambda: 1.5)
    assert loop.tick().rect == Rect(x=15, y=30, width=960, height=720)


def test_the_command_line_the_trainer_starts_the_child_with():
    args = build_parser().parse_args(
        ["--url", GHOST_URL, "--opacity", "0.35", "--visible",
         "--backend", "none", "--fixed-rect", "1,2,640,480"])
    assert args.url == GHOST_URL
    assert args.opacity == pytest.approx(0.35)
    assert args.visible is True
    assert args.backend == "none"
    assert args.fixed_rect == Rect(x=1, y=2, width=640, height=480)


@pytest.mark.parametrize("bad", ["1,2,3", "a,b,c,d", "1,2,3,4,5", ""])
def test_a_malformed_fixed_rect_is_refused_at_the_command_line(bad):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--url", GHOST_URL, "--fixed-rect", bad])


# --- the real child process, with no window anywhere ------------------------

def read_event(proc, waiting_for):
    line = proc.stdout.readline()
    assert line, f"child said nothing while we waited for {waiting_for}"
    return json.loads(line)


def drain_until(proc, waiting_for, satisfied, limit=40):
    """Collect events until the ones we need have all arrived.

    Written against the accumulated list rather than event by event because
    the control thread and the follower both report independently: `applied`
    and `placed` can land in either order.
    """
    seen = []
    for _ in range(limit):
        seen.append(read_event(proc, waiting_for))
        if satisfied(seen):
            return seen
    raise AssertionError(f"never saw {waiting_for}; got {seen}")


@pytest.fixture
def child():
    proc = subprocess.Popen(
        [sys.executable, "-m", "sm64_events.ghost.window",
         "--url", GHOST_URL, "--backend", "none",
         "--fixed-rect", "10,20,640,480", "--follow-period", "0.02"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1, **quiet_spawn_kwargs())
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None and not pipe.closed:
                pipe.close()


def send(proc, payload):
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def test_the_real_child_takes_control_lines_and_dies_with_its_parent(child):
    ready = read_event(child, "the ready event")
    assert ready["event"] == "ready"
    assert ready["backend"] == "none"

    send(child, {"opacity": 0.25, "visible": True})
    seen = drain_until(
        child, "the ghost landing on the picture",
        lambda events: (
            any(e["event"] == "applied" and e.get("opacity") == 0.25 for e in events)
            and any(e["event"] == "placed" and e["show"] for e in events)))
    placed = next(e for e in seen if e["event"] == "placed" and e["show"])
    assert placed["reason"] == "follow"
    assert placed["rect"] == [10, 20, 640, 480]

    send(child, {"visible": False})
    off = drain_until(
        child, "the overlay switching off",
        lambda events: any(e["event"] == "placed" and not e["show"] for e in events))
    hidden = next(e for e in off if e["event"] == "placed" and not e["show"])
    assert hidden["reason"] == "hidden_by_request"
    assert hidden["rect"] is None

    # Closing the pipe is what happens when the trainer dies: no quit line,
    # no signal, just the parent's handle going away.
    child.stdin.close()
    assert child.wait(timeout=20) == 0


def test_the_real_child_exits_on_a_quit_line(child):
    assert read_event(child, "the ready event")["event"] == "ready"
    send(child, {"quit": True})
    assert child.wait(timeout=20) == 0
